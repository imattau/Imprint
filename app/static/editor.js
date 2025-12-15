(() => {
    const MODE_KEY = "imprint.editor.mode";
    let easyMDE = null;
    let currentMode = "markdown";
    let expanded = false;
    let lastScrollTop = 0;
    let handlersBound = false;
    let textareaRef = null;
    let formRef = null;
    let expandButtonRef = null;
    let previewButtonRef = null;
    let modeButtons = [];
    let isPreviewing = false;

    function safeGet(key) {
        try {
            return window.localStorage.getItem(key);
        } catch (_) {
            return null;
        }
    }

    function safeSet(key, value) {
        try {
            window.localStorage.setItem(key, value);
        } catch (_) {
            /* no-op */
        }
    }

    function setExpanded(state) {
        if (state === expanded) return;
        expanded = state;
        const body = document.body;
        if (expanded) {
            lastScrollTop = window.scrollY;
            body.classList.add("editor-expanded");
        } else {
            body.classList.remove("editor-expanded");
            window.scrollTo({ top: lastScrollTop });
        }
        if (expandButtonRef) {
            const icon = expandButtonRef.querySelector(".icon");
            if (icon) {
                icon.textContent = expanded ? "⤡" : "⤢";
            }
            expandButtonRef.setAttribute("title", expanded ? "Collapse editor" : "Expand editor");
            expandButtonRef.setAttribute("aria-pressed", String(expanded));
        }
        refreshEditor();
    }

    function handleEscape(event) {
        if (event.key === "Escape" && expanded) {
            setExpanded(false);
        }
    }

    function updateModeButtons(mode) {
        modeButtons.forEach((button) => {
            const isActive = button.dataset.editorMode === mode;
            button.classList.toggle("active", isActive);
            button.setAttribute("aria-pressed", String(isActive));
        });
    }

    function syncTextareaValue() {
        if (!textareaRef) return;
        if (easyMDE) {
            textareaRef.value = easyMDE.value();
        }
        textareaRef.dispatchEvent(new Event("change", { bubbles: true }));
        textareaRef.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function refreshEditor() {
        if (!easyMDE) return;
        easyMDE.codemirror.refresh();
        window.setTimeout(() => {
            if (easyMDE) {
                easyMDE.codemirror.refresh();
            }
        }, 40);
    }

    function enableVisualMode() {
        if (easyMDE || !window.EasyMDE || !textareaRef || !formRef) {
            return;
        }
        isPreviewing = false;
        easyMDE = new EasyMDE({
            element: textareaRef,
            autoDownloadFontAwesome: false,
            spellChecker: false,
            status: false,
            shortcuts: {
                toggleSideBySide: null,
                toggleFullScreen: null,
            },
            minHeight: "280px",
            toolbar: [
                { name: "bold", action: EasyMDE.toggleBold, title: "Bold", text: "Bold" },
                { name: "italic", action: EasyMDE.toggleItalic, title: "Italic", text: "Italic" },
                { name: "link", action: EasyMDE.drawLink, title: "Link", text: "Link" },
                "|",
                { name: "unordered-list", action: EasyMDE.toggleUnorderedList, title: "Bulleted list", text: "• List" },
                { name: "ordered-list", action: EasyMDE.toggleOrderedList, title: "Numbered list", text: "1. List" },
                { name: "quote", action: EasyMDE.toggleBlockquote, title: "Quote", text: "Quote" },
                { name: "code", action: EasyMDE.toggleCodeBlock, title: "Code", text: "Code" },
            ],
        });
        easyMDE.codemirror.on("change", syncTextareaValue);
        formRef.classList.add("visual-mode");
        syncTextareaValue();
        updatePreviewButton();
        refreshEditor();
    }

    function disableVisualMode() {
        if (!formRef) return;
        disablePreviewState();
        if (easyMDE && textareaRef) {
            textareaRef.value = easyMDE.value();
            easyMDE.toTextArea();
            easyMDE = null;
        }
        formRef.classList.remove("visual-mode");
        syncTextareaValue();
    }

    function setMode(nextMode, opts = { skipStore: false }) {
        const normalized = nextMode === "visual" ? "visual" : "markdown";
        if (normalized === "visual" && !window.EasyMDE) {
            currentMode = "markdown";
            updateModeButtons(currentMode);
            return;
        }

        if (normalized === "visual") {
            enableVisualMode();
        } else {
            disableVisualMode();
        }

        currentMode = normalized;
        updateModeButtons(currentMode);
        updatePreviewButton();
        if (!opts.skipStore) {
            safeSet(MODE_KEY, currentMode);
        }
    }

    function togglePreview() {
        if (!easyMDE) return;
        easyMDE.togglePreview();
        isPreviewing = !isPreviewing;
        updatePreviewButton();
        refreshEditor();
    }

    function disablePreviewState() {
        if (isPreviewing && easyMDE) {
            easyMDE.togglePreview();
        }
        isPreviewing = false;
        updatePreviewButton();
    }

    function updatePreviewButton() {
        if (!previewButtonRef) return;
        const disabled = currentMode !== "visual" || !easyMDE;
        previewButtonRef.disabled = disabled;
        const active = !disabled && isPreviewing;
        previewButtonRef.classList.toggle("active", active);
        previewButtonRef.setAttribute("aria-pressed", String(active));
        previewButtonRef.setAttribute("title", disabled ? "Preview available in Visual mode" : active ? "Exit preview" : "Toggle preview");
    }

    function bindGlobalHandlers() {
        if (handlersBound) return;
        document.addEventListener("keydown", handleEscape);
        if (window.htmx && document.body) {
            document.body.addEventListener("htmx:afterSwap", () => {
                window.requestAnimationFrame(initEditor);
            });
        }
        handlersBound = true;
    }

    function initEditor() {
        const form = document.querySelector(".editor-form");
        const textarea = document.getElementById("content");
        if (!form || !textarea || textarea.dataset.editorBound === "1") {
            return;
        }

        formRef = form;
        textareaRef = textarea;
        expandButtonRef = document.getElementById("toggle-expand");
        previewButtonRef = document.getElementById("toggle-preview");
        modeButtons = Array.from(form.querySelectorAll("[data-editor-mode]"));
        textareaRef.dataset.editorBound = "1";
        setExpanded(false);

        bindGlobalHandlers();

        const defaultMode = form.dataset.defaultMode || ""; // TODO: allow server-side user preference.
        const storedMode = safeGet(MODE_KEY);
        const startingMode =
            storedMode === "visual" || storedMode === "markdown"
                ? storedMode
                : defaultMode === "visual"
                    ? "visual"
                    : "markdown";

        setMode(startingMode, { skipStore: true });

        if (expandButtonRef) {
            expandButtonRef.addEventListener("click", () => setExpanded(!expanded));
        }

        modeButtons.forEach((button) => {
            button.addEventListener("click", () => {
                setMode(button.dataset.editorMode);
            });
        });

        if (previewButtonRef) {
            previewButtonRef.addEventListener("click", () => {
                togglePreview();
            });
        }

        form.addEventListener("submit", () => {
            if (easyMDE) {
                syncTextareaValue();
            }
            disablePreviewState();
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initEditor);
    } else {
        initEditor();
    }
})();
