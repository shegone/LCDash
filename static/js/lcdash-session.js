// Session-end detection for background requests.
//
// The problem this solves, diagnosed from CloudWatch on 2026-08-09:
// ELBAuthFailure was non-zero while ELBAuthError was zero, and AWS defines
// that combination as "an IdP denied access OR an authorization code was used
// more than once". Cognito was accepting the code, so codes were being
// redeemed twice.
//
// The mechanism is this page's own background traffic. The dashboard opens an
// EventSource that auto-reconnects (the server sends `retry: 3000`) and pages
// poll their APIs on a timer. When a session ends, every one of those hits the
// load balancer unauthenticated AT THE SAME TIME as the user's real
// navigation. Each starts its own login flow, they overwrite each other's
// nonce cookie, and one authorization code gets redeemed twice -- the user
// lands on a bare 401 after entering a perfectly good MFA code, and retyping
// the URL "fixes" it because that is a single clean flow with nothing racing.
//
// So background requests must never drive a login. They detect the redirect,
// stop, and hand the decision to the person: one banner, one deliberate
// reload, one flow.
(function () {
    "use strict";

    let ended = false;
    const stoppers = [];

    // A background fetch that was bounced to the identity provider comes back
    // either as an opaque redirect (redirect: "manual") or, having followed
    // the chain, as a login page rather than the JSON that was asked for.
    function looksLikeSignIn(response) {
        if (!response) return false;
        if (response.type === "opaqueredirect" || response.status === 0) return true;
        if (response.status === 401 || response.status === 403) return false; // real API answers
        if (response.redirected) {
            try {
                const target = new URL(response.url);
                if (target.host !== window.location.host) return true;
                if (target.pathname.startsWith("/oauth2/")) return true;
            } catch (error) {
                return false;
            }
        }
        const type = response.headers && response.headers.get("content-type");
        return Boolean(type && type.indexOf("text/html") !== -1);
    }

    function banner() {
        const existing = document.getElementById("lcdash-session-ended");
        if (existing) return existing;
        const element = document.createElement("div");
        element.id = "lcdash-session-ended";
        element.className = "lcdash-session-ended";
        element.setAttribute("role", "alert");
        element.innerHTML =
            '<div>' +
            '<strong>Your session has ended</strong>' +
            '<span>Live updates are stopped. Sign in again to continue.</span>' +
            '</div>';
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = "Sign in again";
        button.addEventListener("click", function () {
            window.location.reload();
        });
        element.appendChild(button);
        document.body.appendChild(element);
        return element;
    }

    const LCDashSession = {
        // Anything that keeps talking to the server registers how to stop.
        onEnd: function (stop) {
            if (typeof stop === "function") stoppers.push(stop);
        },

        hasEnded: function () {
            return ended;
        },

        // Returns true when the caller should abandon its update.
        check: function (response) {
            if (!looksLikeSignIn(response)) return false;
            LCDashSession.end();
            return true;
        },

        end: function () {
            if (ended) return;
            ended = true;
            stoppers.forEach(function (stop) {
                try {
                    stop();
                } catch (error) {
                    /* a stopper that throws must not block the others */
                }
            });
            banner();
        },
    };

    window.LCDashSession = LCDashSession;
})();
