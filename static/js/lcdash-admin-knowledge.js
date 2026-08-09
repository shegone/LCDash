// AI knowledge library administration (/admin/knowledge).
//
// Admins upload PDFs for the assistants, remove mistakes, and run/monitor the
// knowledge-base sync. The backend enforces admin-only access (403 otherwise);
// this script renders honestly whatever the API says. Every failure surfaces
// the HTTP status and the server's `detail` string -- no alert()/confirm()
// anywhere. Removing a document is a two-step inline confirmation, matching
// the account-disable pattern on /admin/users.
//
// The 25 MB / PDF-only checks here are a courtesy; the server is the
// authority and its 4xx detail is always shown verbatim.
(function () {
    "use strict";

    const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
    const CONFIRM_WINDOW_MS = 5000;
    const POLL_INTERVAL_MS = 10000;
    const RUNNING_STATUSES = ["STARTING", "IN_PROGRESS"];
    const DESTINATION_LABELS = {
        mae: "MAE (operations assistant)",
        jack: "JACK (Mindshare radio)",
    };

    const form = document.getElementById("knowledge-upload-form");
    const dropzone = document.getElementById("knowledge-dropzone");
    const fileInput = document.getElementById("knowledge-file-input");
    const filePreview = document.getElementById("knowledge-file-preview");
    const uploadSubmit = document.getElementById("knowledge-upload-submit");
    const uploadProgress = document.getElementById("knowledge-upload-progress");
    const uploadProgressText = document.getElementById("knowledge-upload-progress-text");
    const uploadFeedback = document.getElementById("knowledge-upload-feedback");
    const syncButton = document.getElementById("knowledge-sync-button");
    const syncStatus = document.getElementById("knowledge-sync-status");
    const syncFeedback = document.getElementById("knowledge-sync-feedback");
    const docsLoading = document.getElementById("knowledge-docs-loading");
    const docsError = document.getElementById("knowledge-docs-error");
    const docsEmpty = document.getElementById("knowledge-docs-empty");
    const docsFeedback = document.getElementById("knowledge-docs-feedback");
    const tableWrap = document.getElementById("knowledge-docs-table-wrap");
    const tbody = document.getElementById("knowledge-docs-tbody");
    if (!form || !dropzone || !fileInput || !filePreview || !uploadSubmit ||
        !uploadProgress || !uploadProgressText || !uploadFeedback ||
        !syncButton || !syncStatus || !syncFeedback ||
        !docsLoading || !docsError || !docsEmpty || !docsFeedback ||
        !tableWrap || !tbody) {
        return;
    }

    let selectedFile = null;
    let pollTimer = null;

    // ---------------------------------------------------------------- helpers

    function httpSuffix(status, detail) {
        return status ? ` (HTTP ${status}${detail ? `: ${detail}` : ""})` : " (network error)";
    }

    async function readDetail(response) {
        try {
            const body = await response.json();
            return typeof body.detail === "string" ? body.detail : "";
        } catch (error) {
            return "";
        }
    }

    // Returns {ok, status, detail, payload}; status 0 means the request never
    // reached the server. `body` may be FormData (multipart) or a plain object
    // (JSON).
    async function postRequest(url, body) {
        const options = { method: "POST" };
        if (body instanceof FormData) {
            options.body = body;
        } else {
            options.headers = { "Content-Type": "application/json" };
            options.body = JSON.stringify(body);
        }
        try {
            const response = await fetch(url, options);
            if (!response.ok) {
                return { ok: false, status: response.status, detail: await readDetail(response) };
            }
            let payload = null;
            try {
                payload = await response.json();
            } catch (error) {
                payload = null;
            }
            return { ok: true, status: response.status, detail: "", payload };
        } catch (error) {
            return { ok: false, status: 0, detail: "" };
        }
    }

    function formatTime(value) {
        if (!value) return "";
        if (window.LCDashTime && typeof window.LCDashTime.formatCadDisplayTime === "function") {
            return window.LCDashTime.formatCadDisplayTime(value);
        }
        return String(value);
    }

    function formatBytes(bytes) {
        const n = Number(bytes);
        if (!isFinite(n) || n < 0) return "";
        if (n < 1024) return `${n} B`;
        const units = ["KB", "MB", "GB"];
        let value = n;
        let unit = "B";
        for (let i = 0; i < units.length && value >= 1024; i += 1) {
            value /= 1024;
            unit = units[i];
        }
        return `${value >= 100 ? Math.round(value) : value.toFixed(1)} ${unit}`;
    }

    function destinationLabel(destination) {
        return DESTINATION_LABELS[destination] || String(destination);
    }

    function selectedDestination() {
        const checked = form.querySelector('input[name="knowledge-destination"]:checked');
        return checked ? checked.value : "";
    }

    function looksLikePdf(file) {
        if (file.type === "application/pdf") return true;
        return /\.pdf$/i.test(file.name || "");
    }

    function showFeedback(element, kind, text) {
        element.hidden = false;
        element.className =
            `admin-invite-feedback ${kind === "error" ? "is-error" : "is-success"}`;
        element.textContent = text;
    }

    // ---------------------------------------------------------------- upload

    function refreshUploadControls() {
        uploadSubmit.disabled = !(selectedFile && selectedDestination());
    }

    function setSelectedFile(file) {
        uploadFeedback.hidden = true;
        if (!file) {
            selectedFile = null;
            fileInput.value = "";
            filePreview.hidden = true;
            filePreview.textContent = "";
            refreshUploadControls();
            return;
        }
        if (!looksLikePdf(file)) {
            selectedFile = null;
            fileInput.value = "";
            filePreview.hidden = true;
            showFeedback(uploadFeedback, "error",
                `"${file.name}" is not a PDF. Only PDF documents can join the knowledge library.`);
            refreshUploadControls();
            return;
        }
        if (file.size > MAX_UPLOAD_BYTES) {
            selectedFile = null;
            fileInput.value = "";
            filePreview.hidden = true;
            showFeedback(uploadFeedback, "error",
                `"${file.name}" is ${formatBytes(file.size)} -- larger than the 25 MB limit.`);
            refreshUploadControls();
            return;
        }
        selectedFile = file;
        filePreview.hidden = false;
        filePreview.textContent = `Selected: ${file.name} (${formatBytes(file.size)})`;
        refreshUploadControls();
    }

    dropzone.addEventListener("click", function () {
        fileInput.click();
    });
    dropzone.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {
            event.preventDefault();
            fileInput.click();
        }
    });
    dropzone.addEventListener("dragover", function (event) {
        event.preventDefault();
        dropzone.classList.add("is-dragover");
    });
    dropzone.addEventListener("dragleave", function () {
        dropzone.classList.remove("is-dragover");
    });
    dropzone.addEventListener("drop", function (event) {
        event.preventDefault();
        dropzone.classList.remove("is-dragover");
        const files = event.dataTransfer && event.dataTransfer.files;
        if (files && files.length) setSelectedFile(files[0]);
    });
    fileInput.addEventListener("change", function () {
        setSelectedFile(fileInput.files && fileInput.files[0] ? fileInput.files[0] : null);
    });
    form.querySelectorAll('input[name="knowledge-destination"]').forEach(function (radio) {
        radio.addEventListener("change", refreshUploadControls);
    });

    form.addEventListener("submit", async function (event) {
        event.preventDefault();
        const destination = selectedDestination();
        if (!selectedFile || !destination) return;
        const file = selectedFile;

        uploadSubmit.disabled = true;
        uploadFeedback.hidden = true;
        uploadProgressText.textContent =
            `Uploading ${file.name} to ${destinationLabel(destination)}…`;
        uploadProgress.hidden = false;

        const body = new FormData();
        body.append("file", file, file.name);
        body.append("destination", destination);
        const result = await postRequest("/api/admin/knowledge/documents", body);

        uploadProgress.hidden = true;
        if (!result.ok) {
            refreshUploadControls();
            showFeedback(uploadFeedback, "error",
                `Upload failed${httpSuffix(result.status, result.detail)}`);
            return;
        }
        setSelectedFile(null);
        showFeedback(uploadFeedback, "success",
            `Uploaded ${file.name} to ${destinationLabel(destination)}. ` +
            "Not searchable until the next sync.");
        await loadDocuments(false);
    });

    // ---------------------------------------------------------------- sync

    function humanizeStatKey(key) {
        const words = String(key)
            .replace(/[_-]+/g, " ")
            .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
            .toLowerCase();
        return words.charAt(0).toUpperCase() + words.slice(1);
    }

    function statisticsList(statistics) {
        if (!statistics || typeof statistics !== "object") return null;
        const entries = Object.entries(statistics);
        if (!entries.length) return null;
        const list = document.createElement("ul");
        list.className = "knowledge-sync-stats";
        entries.forEach(function (entry) {
            const item = document.createElement("li");
            item.textContent = `${humanizeStatKey(entry[0])}: ${entry[1]}`;
            list.appendChild(item);
        });
        return list;
    }

    function syncStatusBlock(title, subtitle, statistics, withSpinner) {
        const fragment = document.createDocumentFragment();
        if (withSpinner) {
            const spinner = document.createElement("div");
            spinner.className = "admin-progress-spinner";
            spinner.setAttribute("aria-hidden", "true");
            fragment.appendChild(spinner);
        }
        const text = document.createElement("div");
        const strong = document.createElement("strong");
        strong.textContent = title;
        text.appendChild(strong);
        if (subtitle) {
            const span = document.createElement("span");
            span.className = "knowledge-sync-subtitle";
            span.textContent = subtitle;
            text.appendChild(span);
        }
        const stats = statisticsList(statistics);
        if (stats) text.appendChild(stats);
        fragment.appendChild(text);
        return fragment;
    }

    function stopPolling() {
        if (pollTimer !== null) {
            window.clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    function startPolling() {
        if (pollTimer !== null) return;
        pollTimer = window.setInterval(function () {
            loadDocuments(false);
        }, POLL_INTERVAL_MS);
    }

    function renderIngestion(ingestion) {
        const status = ingestion && ingestion.status ? ingestion.status : "NEVER_RUN";
        const running = RUNNING_STATUSES.includes(status);

        syncStatus.textContent = "";
        syncStatus.className = "knowledge-sync-status";
        if (status === "NEVER_RUN") {
            syncStatus.classList.add("is-neutral");
            syncStatus.appendChild(syncStatusBlock(
                "Uploads have never been synced",
                "New documents are invisible to the assistants until the first sync runs.",
                null, false));
        } else if (running) {
            syncStatus.classList.add("is-running");
            const started = ingestion.started_at
                ? `Started ${formatTime(ingestion.started_at)}.`
                : "";
            syncStatus.appendChild(syncStatusBlock(
                status === "STARTING" ? "Sync starting…" : "Sync in progress…",
                `${started} This page checks again every 10 seconds.`.trim(),
                ingestion.statistics, true));
        } else if (status === "COMPLETE") {
            syncStatus.classList.add("is-complete");
            const started = ingestion.started_at
                ? `Started ${formatTime(ingestion.started_at)}.`
                : "";
            syncStatus.appendChild(syncStatusBlock(
                "Last sync complete", started, ingestion.statistics, false));
        } else if (status === "FAILED") {
            syncStatus.classList.add("is-failed");
            syncStatus.appendChild(syncStatusBlock(
                "Last sync failed",
                "The assistants may be missing recent uploads. Check the statistics below and the Bedrock console.",
                ingestion.statistics, false));
        } else {
            // STOPPED or any status this page does not know yet.
            syncStatus.classList.add("is-neutral");
            syncStatus.appendChild(syncStatusBlock(
                `Last sync: ${status}`, "", ingestion.statistics, false));
        }

        syncButton.disabled = running;
        if (running) {
            startPolling();
        } else {
            stopPolling();
        }
    }

    syncButton.addEventListener("click", async function () {
        syncButton.disabled = true;
        syncFeedback.hidden = true;
        const result = await postRequest("/api/admin/knowledge/sync", {});
        if (!result.ok) {
            syncButton.disabled = false;
            showFeedback(syncFeedback, "error",
                `Sync could not be started${httpSuffix(result.status, result.detail)}`);
            return;
        }
        showFeedback(syncFeedback, "success",
            "Sync started. Status below updates every 10 seconds.");
        if (result.payload && result.payload.status) {
            renderIngestion(result.payload);
        }
        startPolling();
        await loadDocuments(false);
    });

    // ---------------------------------------------------------------- table

    function destinationChip(destination) {
        const chip = document.createElement("span");
        chip.className = `knowledge-destination-chip ${destination === "jack" ? "is-jack" : "is-mae"}`;
        chip.textContent = destination === "jack" ? "JACK" : destination === "mae" ? "MAE" : String(destination);
        chip.title = destinationLabel(destination);
        return chip;
    }

    function setRowBusy(row, busy) {
        row.classList.toggle("is-busy", busy);
        row.setAttribute("aria-busy", busy ? "true" : "false");
        row.querySelectorAll("button").forEach(function (control) {
            control.disabled = busy;
        });
    }

    function setRowNote(row, kind, text) {
        const note = row.querySelector(".admin-row-note");
        if (!note) return;
        if (!text) {
            note.hidden = true;
            note.textContent = "";
            note.className = "admin-row-note";
            return;
        }
        note.hidden = false;
        note.className = `admin-row-note ${kind === "error" ? "is-error" : "is-success"}`;
        note.textContent = text;
    }

    function renderRow(doc) {
        const row = document.createElement("tr");
        row.dataset.documentId = doc.document_id;

        const nameCell = row.insertCell();
        nameCell.className = "knowledge-cell-filename";
        nameCell.textContent = doc.filename;

        const destinationCell = row.insertCell();
        destinationCell.appendChild(destinationChip(doc.destination));

        const sizeCell = row.insertCell();
        sizeCell.className = "knowledge-cell-size";
        sizeCell.textContent = formatBytes(doc.size_bytes);

        const uploadedCell = row.insertCell();
        uploadedCell.className = "knowledge-cell-uploaded";
        uploadedCell.textContent = formatTime(doc.last_modified);

        const uploaderCell = row.insertCell();
        uploaderCell.className = "knowledge-cell-uploader";
        uploaderCell.textContent = doc.uploaded_by || "";

        const actionsCell = row.insertCell();
        actionsCell.className = "knowledge-cell-actions";

        // Two-step inline confirmation: first press arms the button for five
        // seconds, second press removes the document. No window.confirm.
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "admin-row-action is-danger";
        remove.textContent = "Remove";
        remove.setAttribute("aria-label", `Remove ${doc.filename} from the knowledge library`);
        let confirmTimer = null;
        remove.addEventListener("click", async function () {
            if (!remove.classList.contains("is-confirming")) {
                remove.classList.add("is-confirming");
                remove.textContent = "Confirm remove?";
                confirmTimer = window.setTimeout(function () {
                    remove.classList.remove("is-confirming");
                    remove.textContent = "Remove";
                    confirmTimer = null;
                }, CONFIRM_WINDOW_MS);
                return;
            }
            if (confirmTimer !== null) {
                window.clearTimeout(confirmTimer);
                confirmTimer = null;
            }
            setRowBusy(row, true);
            const result = await postRequest("/api/admin/knowledge/documents/remove", {
                destination: doc.destination,
                document_id: doc.document_id,
            });
            if (!result.ok) {
                setRowBusy(row, false);
                remove.classList.remove("is-confirming");
                remove.textContent = "Remove";
                setRowNote(row, "error",
                    `Remove failed${httpSuffix(result.status, result.detail)}`);
                return;
            }
            setRowNote(row, "success",
                "Removed. Its content stays answerable until the next sync.");
            // The row disappears on refresh, so the note is repeated in the
            // list-level live region above the table.
            showFeedback(docsFeedback, "success",
                `Removed ${doc.filename}. Its content stays answerable until the next sync.`);
            await loadDocuments(false);
        });
        actionsCell.appendChild(remove);

        const note = document.createElement("div");
        note.className = "admin-row-note";
        note.hidden = true;
        actionsCell.appendChild(note);

        return row;
    }

    function renderDocuments(documents) {
        tbody.textContent = "";
        if (!documents.length) {
            tableWrap.hidden = true;
            docsEmpty.hidden = false;
            return;
        }
        docsEmpty.hidden = true;
        documents.forEach(function (doc) {
            tbody.appendChild(renderRow(doc));
        });
        tableWrap.hidden = false;
    }

    async function loadDocuments(showSpinner) {
        if (showSpinner) docsLoading.hidden = false;
        docsError.hidden = true;
        try {
            const response = await fetch("/api/admin/knowledge/documents", {
                headers: { "Accept": "application/json" },
            });
            docsLoading.hidden = true;
            if (!response.ok) {
                const detail = await readDetail(response);
                docsError.hidden = false;
                docsError.textContent =
                    `Documents could not be loaded${httpSuffix(response.status, detail)}`;
                return;
            }
            const payload = await response.json();
            renderDocuments(Array.isArray(payload.documents) ? payload.documents : []);
            renderIngestion(payload.ingestion || null);
        } catch (error) {
            docsLoading.hidden = true;
            docsError.hidden = false;
            docsError.textContent = "Documents could not be loaded (network error)";
        }
    }

    refreshUploadControls();
    loadDocuments(true);
})();
