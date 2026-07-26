(function () {
    "use strict";

    const form = document.getElementById("mae-form");
    const questionInput = document.getElementById("mae-question");
    const messages = document.getElementById("mae-messages");
    const thinking = document.getElementById("mae-thinking");
    const sendButton = document.getElementById("mae-send");
    const history = [];
    const entities = {
        cfs_numbers: [],
        unit_numbers: [],
        stations: [],
        addresses: [],
        incidents: []
    };

    function setStatus(cardId, online, text) {
        const card = document.getElementById(cardId);
        if (!card) return;
        card.classList.toggle("is-online", Boolean(online));
        card.classList.toggle("is-offline", !online);
        const value = card.querySelector("strong");
        if (value) value.textContent = text;
    }

    async function loadStatus() {
        try {
            const response = await fetch("/api/mae/status", {cache: "no-store"});
            if (!response.ok) throw new Error("Status unavailable");
            const status = await response.json();
            setStatus(
                "mae-ai-status",
                status.local_ai.connected,
                status.local_ai.connected
                    ? `Online · ${status.local_ai.model}`
                    : "Offline"
            );
            setStatus(
                "mae-db-status",
                status.database.configured && status.database.connected,
                status.database.configured && status.database.connected
                    ? "Connected"
                    : "Unavailable"
            );
            setStatus(
                "mae-cad-status",
                status.centralsquare.configured,
                status.centralsquare.configured
                    ? "Read-only ready"
                    : "Not configured"
            );
        } catch (error) {
            setStatus("mae-ai-status", false, "Unavailable");
            setStatus("mae-db-status", false, "Unavailable");
            setStatus("mae-cad-status", false, "Unavailable");
        }
    }

    function mergeEntities(newEntities) {
        if (!newEntities || typeof newEntities !== "object") return;
        Object.keys(entities).forEach(function (key) {
            const incoming = Array.isArray(newEntities[key])
                ? newEntities[key]
                : [];
            incoming.forEach(function (value) {
                if (value && !entities[key].includes(value)) {
                    entities[key].push(value);
                }
            });
            entities[key] = entities[key].slice(-10);
        });
    }

    function buildEvidence(evidence) {
        if (!Array.isArray(evidence) || !evidence.length) return null;

        const details = document.createElement("details");
        details.className = "mae-evidence";

        const summary = document.createElement("summary");
        summary.innerHTML = `<i class="bi bi-shield-check"></i> View evidence (${evidence.length})`;
        details.appendChild(summary);

        const content = document.createElement("div");
        content.className = "mae-evidence-content";

        evidence.forEach(function (group) {
            const section = document.createElement("section");
            section.className = "mae-evidence-group";

            const heading = document.createElement("div");
            heading.className = "mae-evidence-heading";
            heading.textContent = group.source || "Read-only source";
            section.appendChild(heading);

            const metadata = document.createElement("div");
            metadata.className = "mae-evidence-metadata";
            metadata.textContent = [
                group.kind,
                group.detail,
                group.timestamp
            ].filter(Boolean).join(" · ");
            section.appendChild(metadata);

            (group.items || []).forEach(function (item) {
                const row = document.createElement("div");
                row.className = "mae-evidence-row";

                const label = document.createElement("strong");
                label.textContent = item.label || "Evidence";
                const value = document.createElement("span");
                value.textContent = item.text || "";

                row.append(label, value);
                section.appendChild(row);
            });
            content.appendChild(section);
        });

        details.appendChild(content);
        return details;
    }

    function buildChoices(choices) {
        if (!Array.isArray(choices) || !choices.length) return null;

        const choiceList = document.createElement("div");
        choiceList.className = "mae-choices";
        choices.forEach(function (choice) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "mae-choice-button";
            button.textContent = choice.label || choice.cfs_number || "Select";
            button.addEventListener("click", function () {
                ask(choice.value || choice.cfs_number || "");
            });
            choiceList.appendChild(button);
        });
        return choiceList;
    }

    function buildAssurance(assurance) {
        if (!assurance || typeof assurance !== "object") return null;

        const panel = document.createElement("div");
        const confidence = assurance.confidence || "limited";
        panel.className = `mae-assurance mae-assurance-${confidence}`;

        const heading = document.createElement("strong");
        heading.innerHTML = '<i class="bi bi-shield-check"></i> Answer assurance';

        const confidenceChip = document.createElement("span");
        confidenceChip.className = "mae-assurance-level";
        confidenceChip.textContent = confidence.toUpperCase();

        const details = document.createElement("small");
        details.textContent = [
            assurance.authority,
            assurance.freshness,
            assurance.reason
        ].filter(Boolean).join(" · ");

        panel.append(heading, confidenceChip, details);
        return panel;
    }

    function buildTiming(timing) {
        if (!timing || typeof timing !== "object") return null;
        const totalMs = Number(timing.total_ms || 0);
        if (!totalMs) return null;
        const line = document.createElement("div");
        line.className = "mae-timing";
        line.innerHTML = `<i class="bi bi-stopwatch"></i> ${(totalMs / 1000).toFixed(1)}s total · ${Number(timing.retrieval_ms || 0)}ms research · ${Number(timing.generation_ms || 0)}ms generation`;
        return line;
    }

    async function sendFeedback(interactionId, rating, controls) {
        controls.querySelectorAll("button").forEach(function (button) {
            button.disabled = true;
        });
        const status = controls.querySelector(".mae-feedback-status");
        if (status) status.textContent = "Saving…";

        try {
            const response = await fetch("/api/mae/feedback", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                cache: "no-store",
                body: JSON.stringify({
                    interaction_id: interactionId,
                    rating: rating,
                    comment: ""
                })
            });
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.detail || "Feedback could not be saved.");
            }
            controls.classList.add("is-saved");
            if (status) status.textContent = "Feedback recorded";
        } catch (error) {
            controls.querySelectorAll("button").forEach(function (button) {
                button.disabled = false;
            });
            if (status) status.textContent = error.message || "Unable to save";
        }
    }

    function buildFeedback(interactionId) {
        if (!interactionId) return null;

        const controls = document.createElement("div");
        controls.className = "mae-feedback";

        const label = document.createElement("span");
        label.textContent = "Was this answer useful?";
        controls.appendChild(label);

        [
            ["helpful", "Helpful", "bi-hand-thumbs-up"],
            ["incorrect", "Incorrect", "bi-x-octagon"],
            ["incomplete", "Incomplete", "bi-exclamation-circle"],
            ["wrong_source", "Wrong source", "bi-signpost-split"]
        ].forEach(function (option) {
            const button = document.createElement("button");
            button.type = "button";
            button.dataset.rating = option[0];
            button.innerHTML = `<i class="bi ${option[2]}"></i> ${option[1]}`;
            button.addEventListener("click", function () {
                sendFeedback(interactionId, option[0], controls);
            });
            controls.appendChild(button);
        });

        const status = document.createElement("small");
        status.className = "mae-feedback-status";
        controls.appendChild(status);
        return controls;
    }

    function addMessage(role, content, payload) {
        const responsePayload = payload || {};
        const article = document.createElement("article");
        article.className = `mae-message mae-message-${role}`;

        const avatar = document.createElement("div");
        avatar.className = "mae-avatar";
        const icon = document.createElement("i");
        icon.className = role === "assistant"
            ? "bi bi-stars"
            : "bi bi-person-fill";
        avatar.appendChild(icon);

        const bubble = document.createElement("div");
        bubble.className = "mae-bubble";

        const name = document.createElement("div");
        name.className = "mae-message-name";
        name.textContent = role === "assistant" ? "MAE" : "SUPERVISOR";

        const text = document.createElement("p");
        text.textContent = content;
        bubble.append(name, text);

        const sources = responsePayload.sources;
        if (Array.isArray(sources) && sources.length) {
            const sourceList = document.createElement("div");
            sourceList.className = "mae-sources";
            sources.forEach(function (source) {
                const chip = document.createElement("span");
                chip.className = "mae-source-chip";
                const availability = source.available === false
                    ? " · unavailable"
                    : "";
                chip.textContent = `${source.name} · ${source.detail}${availability}`;
                sourceList.appendChild(chip);
            });
            bubble.appendChild(sourceList);
        }

        const choices = buildChoices(responsePayload.choices);
        if (choices) bubble.appendChild(choices);

        const assurance = buildAssurance(responsePayload.assurance);
        if (assurance) bubble.appendChild(assurance);

        const timing = buildTiming(responsePayload.timing);
        if (timing) bubble.appendChild(timing);

        const evidence = buildEvidence(responsePayload.evidence);
        if (evidence) bubble.appendChild(evidence);

        if (role === "assistant" && responsePayload.audit_saved) {
            const auditBadge = document.createElement("div");
            auditBadge.className = "mae-audit-badge";
            auditBadge.innerHTML = '<i class="bi bi-journal-check"></i> Inquiry audited';
            bubble.appendChild(auditBadge);
        }

        if (role === "assistant") {
            const feedback = buildFeedback(responsePayload.interaction_id);
            if (feedback) bubble.appendChild(feedback);
        }

        article.append(avatar, bubble);
        messages.appendChild(article);
        messages.scrollTop = messages.scrollHeight;
    }

    function setBusy(busy) {
        thinking.hidden = !busy;
        sendButton.disabled = busy;
        questionInput.disabled = busy;
        if (busy) messages.scrollTop = messages.scrollHeight;
    }

    async function ask(question) {
        if (!question) return;
        addMessage("user", question);
        const requestHistory = history.slice(-8);
        history.push({role: "user", content: question});
        setBusy(true);

        try {
            const response = await fetch("/api/mae/chat", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                cache: "no-store",
                body: JSON.stringify({
                    question: question,
                    history: requestHistory,
                    entities: entities
                })
            });
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(
                    payload.detail || "MAE could not complete the inquiry."
                );
            }
            mergeEntities(payload.entities);
            addMessage("assistant", payload.answer, payload);
            history.push({role: "assistant", content: payload.answer});
        } catch (error) {
            addMessage(
                "assistant",
                `I could not complete that inquiry. ${error.message || error}`
            );
        } finally {
            setBusy(false);
            questionInput.focus();
        }
    }

    form.addEventListener("submit", function (event) {
        event.preventDefault();
        const question = questionInput.value.trim();
        if (!question) return;
        questionInput.value = "";
        ask(question);
    });

    questionInput.addEventListener("keydown", function (event) {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            form.requestSubmit();
        }
    });

    document.querySelectorAll("[data-mae-prompt]").forEach(function (button) {
        button.addEventListener("click", function () {
            questionInput.value = button.dataset.maePrompt || "";
            questionInput.focus();
        });
    });

    loadStatus();
})();
