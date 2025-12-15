(function () {
    const debounce = (fn, delay) => {
        let t;
        return (...args) => {
            clearTimeout(t);
            t = setTimeout(() => fn(...args), delay);
        };
    };

    async function fetchEngagement(ids) {
        if (!ids.length) return {};
        const url = `/posts/engagement?ids=${encodeURIComponent(ids.join(","))}`;
        const resp = await fetch(url, { headers: { Accept: "application/json" } });
        if (!resp.ok) return {};
        return resp.json();
    }

    async function refreshEngagement() {
        const shells = document.querySelectorAll(".engagement-shell[data-event-id]");
        const ids = Array.from(shells)
            .map((el) => el.dataset.eventId)
            .filter(Boolean);
        if (!ids.length) return;
        try {
            const data = await fetchEngagement(ids);
            Object.entries(data).forEach(([id, html]) => {
                const target = document.getElementById(`engagement-${id}`);
                if (target) {
                    target.outerHTML = html;
                }
            });
        } catch (err) {
            console.error("Failed to refresh engagement", err);
        }
    }

    const debounced = debounce(refreshEngagement, 200);
    window.refreshEngagementBatch = debounced;

    document.addEventListener("DOMContentLoaded", debounced);
    document.body.addEventListener("authChanged", debounced);
    document.addEventListener("htmx:afterSwap", (evt) => {
        // Refresh when feed fragments are swapped.
        if (evt.detail && evt.detail.elt && evt.detail.elt.closest(".essay-list, .essay-detail")) {
            debounced();
        }
    });
})();
