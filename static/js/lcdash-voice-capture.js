// LCDash cloud voice capture: records a bounded push-to-talk clip as raw
// 16 kHz mono PCM16 (no container) for Amazon Transcribe streaming, which
// only accepts "pcm" or "ogg-opus" - never the "audio/webm;codecs=opus"
// blobs the MediaRecorder path produces for on-prem faster-whisper.
//
// Loaded as a plain <script> tag (no bundler/module system in this
// codebase) and attaches a single global, window.LCDashVoiceCapture.
(function () {
    "use strict";

    var OUTPUT_SAMPLE_RATE_HZ = 16000;
    var MAX_DURATION_SECONDS = 30;
    // The 30-second duration cap binds first at 960,000 bytes, so this is a
    // defensive ceiling rather than the limiting constraint in practice. The
    // server's actual upload cap (app/main.py voice_transcribe_api) is 20 MB.
    var MAX_BYTES = 1900000;
    var BYTES_PER_SAMPLE = 2;
    var SCRIPT_PROCESSOR_BUFFER_SIZE = 4096;
    var MAX_OUTPUT_SAMPLES = Math.min(
        MAX_DURATION_SECONDS * OUTPUT_SAMPLE_RATE_HZ,
        Math.floor(MAX_BYTES / BYTES_PER_SAMPLE)
    );

    // Float32 [-1, 1] -> Int16 [-32768, 32767]. Positive and negative sides
    // use different scale factors (32767 vs 32768) because Int16 range is
    // asymmetric; clamp first so an interpolation overshoot just outside
    // [-1, 1] cannot wrap instead of saturating.
    function floatToInt16(sample) {
        var clamped = sample;
        if (clamped > 1) clamped = 1;
        else if (clamped < -1) clamped = -1;
        var scaled = clamped < 0 ? clamped * 32768 : clamped * 32767;
        scaled = Math.round(scaled);
        if (scaled > 32767) scaled = 32767;
        if (scaled < -32768) scaled = -32768;
        return scaled;
    }

    // Downmix an arbitrary channel count to mono by averaging every channel.
    // In practice the ScriptProcessorNode below is created with
    // numberOfInputChannels = 1, so the browser already forces this - this
    // is a defensive second pass in case a platform ever hands back more.
    function downmixToMono(inputBuffer) {
        var channelCount = inputBuffer.numberOfChannels;
        var length = inputBuffer.length;
        var mono = new Float32Array(length);
        if (channelCount <= 1) {
            if (channelCount === 1) mono.set(inputBuffer.getChannelData(0));
            return mono;
        }
        for (var channel = 0; channel < channelCount; channel += 1) {
            var data = inputBuffer.getChannelData(channel);
            for (var i = 0; i < length; i += 1) {
                mono[i] += data[i];
            }
        }
        for (var j = 0; j < length; j += 1) {
            mono[j] = mono[j] / channelCount;
        }
        return mono;
    }

    // Linear-interpolation resampler from an arbitrary source rate (the
    // AudioContext's *actual* sampleRate, read back after construction since
    // browsers do not reliably honor a requested rate) down to 16 kHz.
    //
    // Model: each callback's mono samples are conceptually appended to an
    // infinite stream. We address that stream through a small "combined"
    // window of the current buffer plus one carried-over sample from the
    // previous buffer (index 0 = previous buffer's last sample, index k
    // (k >= 1) = input[k - 1]). `pos` is a fractional pointer into that
    // window; each output sample is produced by interpolating between
    // combined[floor(pos)] and combined[floor(pos) + 1], then `pos` advances
    // by the input:output ratio. At the end of a buffer, `pos` minus the
    // buffer length becomes the carried-over pointer for the next callback,
    // and the buffer's true last sample becomes the next callback's
    // "previous sample" - so the fractional position and the interpolation
    // neighborhood both survive the callback boundary with no gap, no
    // duplicated sample, and no discontinuity.
    function createResampler(inputSampleRateHz) {
        var ratio = inputSampleRateHz / OUTPUT_SAMPLE_RATE_HZ;
        // pos starts at 1 so the very first output sample lands exactly on
        // input[0] (combined index 1) with a zero fractional part - i.e. no
        // time-shift at the very start of capture, and the unused
        // combined[0] ("previous sample", not yet known) never contributes.
        var pos = 1;
        var prevSample = 0;

        function combinedAt(input, index) {
            return index === 0 ? prevSample : input[index - 1];
        }

        return {
            // input: Float32Array of mono samples for one onaudioprocess
            // callback. Returns a plain Array of resampled Float32 samples
            // (may be empty if the ratio means this callback yields none).
            push: function (input) {
                var length = input.length;
                var out = [];
                while (pos < length) {
                    var base = Math.floor(pos);
                    var frac = pos - base;
                    var s0 = combinedAt(input, base);
                    var s1 = combinedAt(input, base + 1);
                    out.push(s0 + (s1 - s0) * frac);
                    pos += ratio;
                }
                if (length > 0) {
                    prevSample = input[length - 1];
                    pos -= length;
                }
                return out;
            }
        };
    }

    // Begin capturing from an already-open MediaStream via its existing
    // AudioContext + MediaStreamAudioSourceNode (the caller's level-
    // monitoring source node is reused rather than opening a second tap on
    // the same stream). Returns a handle; call handle.stop() to finish.
    function start(audioContext, sourceNode) {
        if (!audioContext || !sourceNode) {
            throw new Error(
                "LCDashVoiceCapture.start requires an AudioContext and a MediaStreamAudioSourceNode."
            );
        }

        // Safari and some platforms silently ignore a requested sampleRate
        // and keep the hardware native rate (commonly 48000 or 44100), so
        // the resampler is always built from the context's real rate.
        var resampler = createResampler(audioContext.sampleRate);
        var samples = new Int16Array(MAX_OUTPUT_SAMPLES);
        var writeIndex = 0;
        var stopped = false;

        var processor = audioContext.createScriptProcessor(
            SCRIPT_PROCESSOR_BUFFER_SIZE, 1, 1
        );
        // A ScriptProcessorNode only fires onaudioprocess while it is part
        // of a live graph reaching the destination. Route it through a
        // muted gain node so the microphone is never audible/echoed back
        // while still keeping the node "pulled" by the audio thread.
        var silentSink = audioContext.createGain();
        silentSink.gain.value = 0;

        processor.onaudioprocess = function (event) {
            if (stopped || writeIndex >= MAX_OUTPUT_SAMPLES) return;
            var mono = downmixToMono(event.inputBuffer);
            var resampled = resampler.push(mono);
            for (var i = 0; i < resampled.length && writeIndex < MAX_OUTPUT_SAMPLES; i += 1) {
                samples[writeIndex] = floatToInt16(resampled[i]);
                writeIndex += 1;
            }
        };

        sourceNode.connect(processor);
        processor.connect(silentSink);
        silentSink.connect(audioContext.destination);

        function disconnectGraph() {
            try {
                sourceNode.disconnect(processor);
            } catch (error) {
                // Already disconnected (e.g. the stream ended); safe to ignore.
            }
            try {
                processor.disconnect(silentSink);
            } catch (error) {
                // Already disconnected; safe to ignore.
            }
            try {
                silentSink.disconnect(audioContext.destination);
            } catch (error) {
                // Already disconnected; safe to ignore.
            }
            processor.onaudioprocess = null;
        }

        // Stops capture, disconnects the ScriptProcessorNode (leaving the
        // caller's sourceNode -> analyser connection untouched so level
        // monitoring keeps working across repeated start/stop cycles), and
        // resolves the captured clip. Returns null when nothing usable was
        // captured so the caller can treat it the same as "no speech
        // detected".
        function stop() {
            if (stopped) return Promise.resolve(null);
            stopped = true;
            disconnectGraph();

            if (writeIndex <= 0) return Promise.resolve(null);

            var durationSeconds = writeIndex / OUTPUT_SAMPLE_RATE_HZ;
            var buffer = new ArrayBuffer(writeIndex * BYTES_PER_SAMPLE);
            var view = new DataView(buffer);
            for (var i = 0; i < writeIndex; i += 1) {
                view.setInt16(i * BYTES_PER_SAMPLE, samples[i], true);
            }
            var blob = new Blob([buffer], {type: "application/octet-stream"});
            return Promise.resolve({
                blob: blob,
                sampleRateHz: OUTPUT_SAMPLE_RATE_HZ,
                durationSeconds: durationSeconds,
                audioFormat: "pcm"
            });
        }

        return {stop: stop};
    }

    window.LCDashVoiceCapture = {
        start: start
    };
})();
