(function () {
    "use strict";

    const MOBILE_BREAKPOINT = 992;
    const sidebar = document.getElementById("lcdash-sidebar");
    const overlay = document.getElementById("mobile-nav-overlay");
    const openButton = document.getElementById("mobile-menu-button");
    const closeButton = document.getElementById("mobile-nav-close");

    function isMobileLayout() {
        return window.innerWidth < MOBILE_BREAKPOINT;
    }

    function openNavigation() {
        if (!sidebar || !overlay || !openButton || !isMobileLayout()) {
            return;
        }

        sidebar.classList.add("mobile-open");
        overlay.classList.add("mobile-open");
        document.body.classList.add("mobile-nav-open");
        openButton.setAttribute("aria-expanded", "true");
        overlay.setAttribute("aria-hidden", "false");
        closeButton?.focus();
    }

    function closeNavigation(returnFocus) {
        if (!sidebar || !overlay || !openButton) {
            return;
        }

        sidebar.classList.remove("mobile-open");
        overlay.classList.remove("mobile-open");
        document.body.classList.remove("mobile-nav-open");
        openButton.setAttribute("aria-expanded", "false");
        overlay.setAttribute("aria-hidden", "true");

        if (returnFocus) {
            openButton.focus();
        }
    }

    openButton?.addEventListener("click", openNavigation);
    closeButton?.addEventListener("click", function () {
        closeNavigation(true);
    });
    overlay?.addEventListener("click", function () {
        closeNavigation(true);
    });

    sidebar?.querySelectorAll("a.nav-link").forEach(function (link) {
        link.addEventListener("click", function () {
            if (isMobileLayout()) {
                closeNavigation(false);
            }
        });
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
            closeNavigation(true);
        }
    });

    window.addEventListener("resize", function () {
        if (!isMobileLayout()) {
            closeNavigation(false);
        }
    });

    if ("serviceWorker" in navigator && window.isSecureContext) {
        window.addEventListener("load", function () {
            navigator.serviceWorker.register("/static/service-worker.js", {
                scope: "/"
            }).then(function (registration) {
                return registration.update();
            }).catch(function () {
                // The dashboard remains fully functional if installation support is unavailable.
            });
        });
    }
})();
