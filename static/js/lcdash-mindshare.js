(function () {
    const form = document.getElementById("mindshare-form");
    const questionInput = document.getElementById("mindshare-question");
    const sendButton = document.getElementById("mindshare-send");
    const messages = document.getElementById("mindshare-messages");
    const thinking = document.getElementById("mindshare-thinking");
    const history = [];

    if (!form || !questionInput || !messages) return;

    function updateStatusCard(id, online, text) {
        const card = document.getElementById(id);
        if (!card) return;
        card.classList.toggle("is-online", online);
        card.classList.toggle("is-offline", !online);
        const value = card.querySelector("strong");
        if (value) value.textContent = text;
    }

    async function loadStatus() {
        try {
            const response = await fetch("/api/mindshare/status", {
                cache: "no-store"
            });
            const payload = await response.json();
            updateStatusCard(
                "mindshare-ai-status",
                Boolean(payload.assistant && payload.assistant.connected),
                payload.assistant && payload.assistant.connected
                    ? payload.assistant.model
                    : "Unavailable"
            );
            const knowledge = payload.knowledge || {};
            updateStatusCard(
                "mindshare-library-status",
                Boolean(knowledge.connected && knowledge.documents),
                knowledge.connected
                    ? `${knowledge.documents || 0} documents`
                    : "Unavailable"
            );
        } catch (error) {
            updateStatusCard("mindshare-ai-status", false, "Unavailable");
            updateStatusCard("mindshare-library-status", false, "Unavailable");
        }
    }

    function addEvidence(bubble, evidence) {
        if (!Array.isArray(evidence) || !evidence.length) return;
        const details = document.createElement("details");
        details.className = "mae-evidence";
        const summary = document.createElement("summary");
        summary.textContent = `Supporting documents (${evidence.length})`;
        const content = document.createElement("div");
        content.className = "mae-evidence-content";

        evidence.forEach(function (item) {
            const group = document.createElement("div");
            group.className = "mae-evidence-group";
            const heading = document.createElement("div");
            heading.className = "mae-evidence-heading";
            heading.textContent = item.title || item.file_name || "Mindshare document";
            const metadata = document.createElement("div");
            metadata.className = "mae-evidence-metadata";
            metadata.textContent = item.page_number
                ? `Page ${item.page_number}`
                : "Page not reported";
            const passage = document.createElement("p");
            passage.textContent = item.content || "";
            group.append(heading, metadata, passage);
            content.appendChild(group);
        });
        details.append(summary, content);
        bubble.appendChild(details);
    }

    function addMessage(role, content, payload) {
        const article = document.createElement("article");
        article.className = `mae-message mae-message-${role}`;
        const avatar = document.createElement("div");
        avatar.className = "mae-avatar";
        avatar.innerHTML = role === "assistant"
            ? '<i class="bi bi-broadcast-pin"></i>'
            : '<i class="bi bi-person-fill"></i>';
        const bubble = document.createElement("div");
        bubble.className = "mae-bubble";
        const name = document.createElement("div");
        name.className = "mae-message-name";
        name.textContent = role === "assistant" ? "MTA" : "USER";
        const text = document.createElement("p");
        text.textContent = content;
        bubble.append(name, text);

        if (payload && payload.assurance) {
            const assurance = document.createElement("div");
            assurance.className = `mae-assurance mae-assurance-${payload.assurance.level || "supported"}`;
            assurance.innerHTML = `
                <strong>${payload.assurance.label || "Documentation supported"}</strong>
                <span class="mae-assurance-level">${(payload.assurance.level || "supported").toUpperCase()}</span>
                <small>${payload.assurance.detail || ""}</small>
            `;
            bubble.appendChild(assurance);
        }
        addEvidence(bubble, payload && payload.evidence);
        article.append(avatar, bubble);
        messages.appendChild(article);
        messages.scrollTop = messages.scrollHeight;
    }

    function setBusy(busy) {
        thinking.hidden = !busy;
        sendButton.disabled = busy;
        questionInput.disabled = busy;
    }

    async function ask(question) {
        const requestHistory = history.slice(-6);
        addMessage("user", question);
        history.push({role: "user", content: question});
        setBusy(true);
        try {
            const response = await fetch("/api/mindshare/chat", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                cache: "no-store",
                body: JSON.stringify({
                    question: question,
                    history: requestHistory
                })
            });
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.detail || "The inquiry could not be completed.");
            }
            addMessage("assistant", payload.answer, payload);
            history.push({role: "assistant", content: payload.answer});
        } catch (error) {
            addMessage(
                "assistant",
                `I could not complete that inquiry. ${error.message || String(error)}`
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

    document.querySelectorAll("[data-mindshare-prompt]").forEach(function (button) {
        button.addEventListener("click", function () {
            questionInput.value = button.dataset.mindsharePrompt || "";
            questionInput.focus();
        });
    });

    loadStatus();
})();
