(function () {
    "use strict";

    const form = document.getElementById("mae-form");
    const questionInput = document.getElementById("mae-question");
    const messages = document.getElementById("mae-messages");
    const thinking = document.getElementById("mae-thinking");
    const sendButton = document.getElementById("mae-send");
    const history = [];

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
                status.centralsquare.configured ? "Read-only ready" : "Not configured"
            );
        } catch (error) {
            setStatus("mae-ai-status", false, "Unavailable");
            setStatus("mae-db-status", false, "Unavailable");
            setStatus("mae-cad-status", false, "Unavailable");
        }
    }

    function addMessage(role, content, sources) {
        const article = document.createElement("article");
        article.className = `mae-message mae-message-${role}`;

        const avatar = document.createElement("div");
        avatar.className = "mae-avatar";
        const icon = document.createElement("i");
        icon.className = role === "assistant" ? "bi bi-stars" : "bi bi-person-fill";
        avatar.appendChild(icon);

        const bubble = document.createElement("div");
        bubble.className = "mae-bubble";

        const name = document.createElement("div");
        name.className = "mae-message-name";
        name.textContent = role === "assistant" ? "MAE" : "SUPERVISOR";

        const text = document.createElement("p");
        text.textContent = content;
        bubble.append(name, text);

        if (Array.isArray(sources) && sources.length) {
            const sourceList = document.createElement("div");
            sourceList.className = "mae-sources";
            sources.forEach(function (source) {
                const chip = document.createElement("span");
                chip.className = "mae-source-chip";
                const availability = source.available === false ? " · unavailable" : "";
                chip.textContent = `${source.name} · ${source.detail}${availability}`;
                sourceList.appendChild(chip);
            });
            bubble.appendChild(sourceList);
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
                    history: requestHistory
                })
            });
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.detail || "MAE could not complete the inquiry.");
            }
            addMessage("assistant", payload.answer, payload.sources);
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
