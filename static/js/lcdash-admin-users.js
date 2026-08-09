// Cognito account management for the LCDash cloud pilot (/admin/users).
//
// The backend enforces admin-only access (403 otherwise); this script just
// renders honestly whatever the API says. Every failure surfaces the HTTP
// status and the server's `detail` string -- no generic alert() anywhere.
// Disabling an account is a two-step inline confirmation because there is no
// delete: disabled accounts are retained for audit.
(function () {
    "use strict";

    const ROLES = ["user", "supervisor", "admin"];
    const ROLE_ORDER = { admin: 0, supervisor: 1, user: 2 };
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

    // Sort: enabled admins, then supervisors, then users, disabled accounts
    // last; alphabetical by email within each band.
    function sortUsers(users) {
        return users.slice().sort(function (a, b) {
            const bandA = a.enabled ? (ROLE_ORDER[a.role] !== undefined ? ROLE_ORDER[a.role] : 2) : 3;
            const bandB = b.enabled ? (ROLE_ORDER[b.role] !== undefined ? ROLE_ORDER[b.role] : 2) : 3;
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
            option.textContent = role.charAt(0).toUpperCase() + role.slice(1);
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
