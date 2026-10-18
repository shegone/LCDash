// Cognito account management for the LCDash cloud pilot (/admin/users).
//
// The backend enforces admin-only access (403 otherwise); this script just
// renders honestly whatever the API says. Every failure surfaces the HTTP
// status and the server's `detail` string -- no generic alert() anywhere.
// Disable, Reset password, and Delete are each a two-step inline confirmation
// (no window.confirm). Delete is permanent by owner decision 2026-10-17 and
// needs the target's email retyped; Disable keeps the record. The page never
// sees a password: Cognito emails the code or temporary password to the user.
// All server-supplied text is written with textContent, never innerHTML.
(function () {
    "use strict";

    // Read synchronously while this script is executing; null afterwards.
    const thisScript = document.currentScript;
    const currentUserEmail = String(
        (thisScript && thisScript.dataset.currentUserEmail) || ""
    ).trim().toLowerCase();

    // "avatar" is the conversation-only tier from the avatar plan: the
    // account can talk with MAE at /mae/avatar and reach nothing else.
    // "dispatcher" is supervisor minus MAE Avatar, Mindshare/JACK, and
    // Tools & Quality.
    // "fire_officer" is live operations with full call detail and nothing
    // else: no analytics, reports, knowledge, MAE, or tools.
    const ROLES = ["user", "supervisor", "dispatcher", "fire_officer", "admin", "avatar"];
    const ROLE_ORDER = { admin: 0, supervisor: 1, dispatcher: 2, fire_officer: 3, user: 4, avatar: 5 };
    const DISABLED_BAND = 6;
    const CONFIRM_WINDOW_MS = 5000;

    const loading = document.getElementById("admin-users-loading");
    const errorBox = document.getElementById("admin-users-error");
    const emptyBox = document.getElementById("admin-users-empty");
    const tableWrap = document.getElementById("admin-users-table-wrap");
    const tbody = document.getElementById("admin-users-tbody");
    const form = document.getElementById("admin-invite-form");
    const emailInput = document.getElementById("admin-invite-email");
    const roleSelect = document.getElementById("admin-invite-role");
    const submitButton = document.getElementById("admin-invite-submit");
    const feedback = document.getElementById("admin-invite-feedback");
    if (!loading || !errorBox || !emptyBox || !tableWrap || !tbody ||
        !form || !emailInput || !roleSelect || !submitButton || !feedback) {
        return;
    }

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
    // reached the server.
    async function postJson(url, body) {
        try {
            const response = await fetch(url, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });
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

    function formatCreated(value) {
        if (!value) return "";
        if (window.LCDashTime && typeof window.LCDashTime.formatCadDisplayTime === "function") {
            return window.LCDashTime.formatCadDisplayTime(value);
        }
        return String(value);
    }

    // Sort: enabled admins, then supervisors, then users, then avatar-only
    // accounts, disabled accounts last; alphabetical by email within each band.
    function sortUsers(users) {
        return users.slice().sort(function (a, b) {
            const bandA = a.enabled ? (ROLE_ORDER[a.role] !== undefined ? ROLE_ORDER[a.role] : 2) : DISABLED_BAND;
            const bandB = b.enabled ? (ROLE_ORDER[b.role] !== undefined ? ROLE_ORDER[b.role] : 2) : DISABLED_BAND;
            if (bandA !== bandB) return bandA - bandB;
            return String(a.email).localeCompare(String(b.email));
        });
    }

    function statusChip(user) {
        const chip = document.createElement("span");
        chip.className = "admin-status-chip";
        if (!user.enabled) {
            chip.classList.add("is-disabled");
            chip.textContent = "Disabled";
        } else if (user.invited_pending) {
            chip.classList.add("is-pending");
            chip.textContent = "Invite pending";
        } else {
            chip.classList.add("is-active");
            chip.textContent = "Active";
        }
        return chip;
    }

    function setRowBusy(row, busy) {
        row.classList.toggle("is-busy", busy);
        row.setAttribute("aria-busy", busy ? "true" : "false");
        row.querySelectorAll("button, select").forEach(function (control) {
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

    function actionButton(label, extraClass) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `admin-row-action${extraClass ? ` ${extraClass}` : ""}`;
        button.textContent = label;
        return button;
    }

    function renderRow(user) {
        const row = document.createElement("tr");
        row.dataset.email = user.email;

        const emailCell = row.insertCell();
        emailCell.className = "admin-cell-email";
        emailCell.textContent = user.email;

        const roleCell = row.insertCell();
        const select = document.createElement("select");
        select.className = "admin-role-select";
        select.setAttribute("aria-label", `Role for ${user.email}`);
        ROLES.forEach(function (role) {
            const option = document.createElement("option");
            option.value = role;
            option.textContent = role === "fire_officer"
                ? "Fire Officer"
                : role.charAt(0).toUpperCase() + role.slice(1);
            select.appendChild(option);
        });
        select.value = ROLES.includes(user.role) ? user.role : "user";
        select.addEventListener("change", async function () {
            const previous = user.role;
            const next = select.value;
            setRowBusy(row, true);
            const result = await postJson("/api/admin/users/role", {
                email: user.email,
                role: next,
            });
            if (!result.ok) {
                setRowBusy(row, false);
                select.value = previous;
                setRowNote(row, "error",
                    `Role change failed${httpSuffix(result.status, result.detail)}`);
                return;
            }
            // Reload so the new role lands in the right sort band.
            await loadUsers(false);
        });
        roleCell.appendChild(select);

        const statusCell = row.insertCell();
        statusCell.appendChild(statusChip(user));

        const createdCell = row.insertCell();
        createdCell.className = "admin-cell-created";
        createdCell.textContent = formatCreated(user.created_at);

        const actionsCell = row.insertCell();
        actionsCell.className = "admin-cell-actions";
        const actionsWrap = document.createElement("div");
        actionsWrap.className = "admin-row-actions";

        if (user.invited_pending) {
            const resend = actionButton("Resend invite");
            resend.setAttribute("aria-label", `Resend invite to ${user.email}`);
            resend.addEventListener("click", async function () {
                setRowBusy(row, true);
                const result = await postJson("/api/admin/users/resend-invite", {
                    email: user.email,
                });
                setRowBusy(row, false);
                if (!result.ok) {
                    setRowNote(row, "error",
                        `Resend failed${httpSuffix(result.status, result.detail)}`);
                    return;
                }
                setRowNote(row, "success", `Invite re-sent to ${user.email}.`);
            });
            actionsWrap.appendChild(resend);
        }

        if (user.enabled) {
            // Two-step inline confirmation: first press arms the button for
            // five seconds, second press disables the account. No window.confirm.
            const disable = actionButton("Disable", "is-danger");
            disable.setAttribute("aria-label", `Disable account ${user.email}`);
            let confirmTimer = null;
            disable.addEventListener("click", async function () {
                if (!disable.classList.contains("is-confirming")) {
                    disable.classList.add("is-confirming");
                    disable.textContent = "Confirm disable?";
                    confirmTimer = window.setTimeout(function () {
                        disable.classList.remove("is-confirming");
                        disable.textContent = "Disable";
                        confirmTimer = null;
                    }, CONFIRM_WINDOW_MS);
                    return;
                }
                if (confirmTimer !== null) {
                    window.clearTimeout(confirmTimer);
                    confirmTimer = null;
                }
                setRowBusy(row, true);
                const result = await postJson("/api/admin/users/disable", {
                    email: user.email,
                });
                if (!result.ok) {
                    setRowBusy(row, false);
                    disable.classList.remove("is-confirming");
                    disable.textContent = "Disable";
                    setRowNote(row, "error",
                        `Disable failed${httpSuffix(result.status, result.detail)}`);
                    return;
                }
                await loadUsers(false);
            });
            actionsWrap.appendChild(disable);
        } else {
            const enable = actionButton("Enable");
            enable.setAttribute("aria-label", `Enable account ${user.email}`);
            enable.addEventListener("click", async function () {
                setRowBusy(row, true);
                const result = await postJson("/api/admin/users/enable", {
                    email: user.email,
                });
                if (!result.ok) {
                    setRowBusy(row, false);
                    setRowNote(row, "error",
                        `Enable failed${httpSuffix(result.status, result.detail)}`);
                    return;
                }
                await loadUsers(false);
            });
            actionsWrap.appendChild(enable);
        }

        // Reset and Delete are never offered on the signed-in admin's own row
        // (the server refuses both as well). Reset needs an enabled account.
        const isOwnRow = currentUserEmail !== "" &&
            String(user.email).trim().toLowerCase() === currentUserEmail;
        // If the page could not tell us who is signed in, offer neither action
        // (fail closed; the server refuses a self-delete or self-reset anyway).
        if (currentUserEmail !== "" && !isOwnRow) {
            let openPanel = null;
            let opener = null;
            const closePanel = function () {
                if (openPanel) {
                    openPanel.remove();
                    openPanel = null;
                }
                if (opener && document.body.contains(opener)) {
                    opener.focus();
                }
            };
            const newPanel = function (extraClass) {
                if (openPanel) {
                    openPanel.remove();
                    openPanel = null;
                }
                const panel = document.createElement("div");
                panel.className = `admin-row-panel ${extraClass}`;
                panel.addEventListener("keydown", function (event) {
                    if (event.key === "Escape") {
                        event.preventDefault();
                        closePanel();
                    }
                });
                actionsWrap.appendChild(panel);
                openPanel = panel;
                return panel;
            };
            const panelButtons = function (panel) {
                const buttons = document.createElement("div");
                buttons.className = "admin-row-panel-buttons";
                panel.appendChild(buttons);
                return buttons;
            };

            if (user.enabled) {
                const reset = actionButton("Reset password");
                reset.setAttribute("aria-label", `Reset password for ${user.email}`);
                reset.addEventListener("click", function () {
                    setRowNote(row, "", "");
                    opener = reset;
                    const panel = newPanel("is-reset");
                    const message = document.createElement("p");
                    message.textContent =
                        `Cognito will email ${user.email} a code (or a new temporary ` +
                        "password if they never signed in). You will not see the password.";
                    panel.appendChild(message);
                    const buttons = panelButtons(panel);
                    const confirm = actionButton("Confirm reset");
                    const cancel = actionButton("Cancel");
                    cancel.addEventListener("click", closePanel);
                    confirm.addEventListener("click", async function () {
                        setRowBusy(row, true);
                        const result = await postJson("/api/admin/users/reset-password", {
                            email: user.email,
                        });
                        setRowBusy(row, false);
                        if (!result.ok) {
                            setRowNote(row, "error",
                                `Reset failed${httpSuffix(result.status, result.detail)}`);
                            return;
                        }
                        closePanel();
                        const method = result.payload && result.payload.method;
                        setRowNote(row, "success", method === "invitation_resent"
                            ? `${user.email} never signed in, so a new temporary password was emailed with a fresh invitation.`
                            : `A password reset code was emailed to ${user.email}. They set a new password at next sign-in.`);
                    });
                    buttons.appendChild(confirm);
                    buttons.appendChild(cancel);
                });
                actionsWrap.appendChild(reset);
            }

            const del = actionButton("Delete", "is-danger");
            del.setAttribute("aria-label", `Delete account ${user.email}`);
            del.addEventListener("click", function () {
                setRowNote(row, "", "");
                opener = del;
                const panel = newPanel("is-delete");
                // Name the account being deleted: on a phone the email column
                // scrolls out of view while this panel is open.
                const target = document.createElement("p");
                target.className = "admin-row-panel-target";
                target.textContent = `Account to delete: ${user.email}`;
                panel.appendChild(target);
                const label = document.createElement("label");
                label.textContent = "Type the user's email to confirm";
                const input = document.createElement("input");
                input.type = "text";
                input.autocomplete = "off";
                input.spellcheck = false;
                input.setAttribute("aria-label", `Type ${user.email} to confirm deletion`);
                label.appendChild(document.createElement("br"));
                label.appendChild(input);
                panel.appendChild(label);
                const warning = document.createElement("p");
                warning.textContent =
                    "This permanently removes the account and cannot be undone. " +
                    "To keep the record but block sign-in, use Disable instead.";
                panel.appendChild(warning);
                const buttons = panelButtons(panel);
                const confirm = actionButton("Delete permanently", "is-destructive");
                const cancel = actionButton("Cancel");
                const matches = function () {
                    return input.value.trim().toLowerCase() ===
                        String(user.email).trim().toLowerCase();
                };
                const syncConfirm = function () {
                    confirm.disabled = !matches();
                };
                syncConfirm();
                input.addEventListener("input", syncConfirm);
                cancel.addEventListener("click", closePanel);
                confirm.addEventListener("click", async function () {
                    if (!matches()) return;
                    setRowBusy(row, true);
                    const result = await postJson("/api/admin/users/delete", {
                        email: user.email,
                        confirm_email: input.value.trim(),
                    });
                    if (!result.ok) {
                        setRowBusy(row, false);
                        syncConfirm();
                        setRowNote(row, "error",
                            `Delete failed${httpSuffix(result.status, result.detail)}`);
                        return;
                    }
                    row.remove();
                    if (!tbody.children.length) {
                        tableWrap.hidden = true;
                        emptyBox.hidden = false;
                    } else {
                        // Keep keyboard users in the table instead of dropping to <body>.
                        const next = tbody.querySelector("button");
                        if (next) next.focus();
                    }
                });
                buttons.appendChild(confirm);
                buttons.appendChild(cancel);
                input.focus();
            });
            actionsWrap.appendChild(del);
        }

        actionsCell.appendChild(actionsWrap);

        const note = document.createElement("div");
        note.className = "admin-row-note";
        note.hidden = true;
        actionsCell.appendChild(note);

        return row;
    }

    function renderUsers(users) {
        tbody.textContent = "";
        if (!users.length) {
            tableWrap.hidden = true;
            emptyBox.hidden = false;
            return;
        }
        emptyBox.hidden = true;
        sortUsers(users).forEach(function (user) {
            tbody.appendChild(renderRow(user));
        });
        tableWrap.hidden = false;
    }

    async function loadUsers(showSpinner) {
        if (showSpinner) loading.hidden = false;
        errorBox.hidden = true;
        try {
            const response = await fetch("/api/admin/users", {
                headers: { "Accept": "application/json" },
            });
            loading.hidden = true;
            if (!response.ok) {
                const detail = await readDetail(response);
                errorBox.hidden = false;
                errorBox.textContent =
                    `Accounts could not be loaded${httpSuffix(response.status, detail)}`;
                return;
            }
            const payload = await response.json();
            renderUsers(Array.isArray(payload.users) ? payload.users : []);
        } catch (error) {
            loading.hidden = true;
            errorBox.hidden = false;
            errorBox.textContent = "Accounts could not be loaded (network error)";
        }
    }

    function showInviteFeedback(kind, text) {
        feedback.hidden = false;
        feedback.className =
            `admin-invite-feedback ${kind === "error" ? "is-error" : "is-success"}`;
        feedback.textContent = text;
    }

    form.addEventListener("submit", async function (event) {
        event.preventDefault();
        const email = emailInput.value.trim();
        if (!email) return;
        const role = roleSelect.value;
        submitButton.disabled = true;
        feedback.hidden = true;
        const result = await postJson("/api/admin/users", { email: email, role: role });
        submitButton.disabled = false;
        if (!result.ok) {
            showInviteFeedback("error",
                `Invite failed${httpSuffix(result.status, result.detail)}`);
            return;
        }
        form.reset();
        roleSelect.value = "supervisor";
        showInviteFeedback("success", `Invite emailed to ${email}`);
        await loadUsers(false);
    });

    loadUsers(true);
})();
