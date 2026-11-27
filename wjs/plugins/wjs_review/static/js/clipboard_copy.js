/**
 * Generic "copy to clipboard" button.
 *
 * Markup contract (see wjs_review/details/elements/author_notes_copy_button.html):
 *   <button data-copy-source="template[data-some-source]" data-copy-label="Copy" data-copied-label="Copied">
 *     <i class="bi bi-clipboard"></i> <span data-copy-label-text>Copy</span>
 *   </button>
 *   <span data-copy-error hidden>...</span>   <!-- sibling of the button, shown when the copy fails -->
 *
 * `data-copy-source` is a CSS selector for the <template> holding the stored HTML to copy. It is resolved inside the
 * button's closest `[data-copy-root]` ancestor, not on the whole document, so the same content can be rendered more
 * than once on a page (no ids to keep unique) and every button copies its own. The clipboard receives both
 * `text/html` (formatting preserved for rich-text targets) and `text/plain` (for plain-text editors).
 */
(function () {
  "use strict";

  const FEEDBACK_MS = 2000;
  const BLOCK_SELECTOR = "p, div, ul, ol, li, br, h1, h2, h3, h4, h5, h6, table, blockquote, pre";
  const timers = new WeakMap();

  /**
   * Return the HTML and the plain text of the content of a <template>.
   *
   * innerText ignores newlines (it behaves like textContent) when the element is not rendered, so the content is
   * parked off-screen but displayed before reading it.
   */
  function extractContent(template) {
    const holder = document.createElement("div");
    holder.appendChild(template.content.cloneNode(true));
    /* Legacy notes may be plain text with raw newlines and no tags: keep their line breaks. */
    if (!holder.querySelector(BLOCK_SELECTOR)) {
      holder.innerHTML = holder.innerHTML.replace(/\r?\n/g, "<br>");
    }
    const html = holder.innerHTML.trim();

    holder.style.position = "absolute";
    holder.style.left = "-9999px";
    holder.style.top = "0";
    holder.style.display = "block";
    document.body.appendChild(holder);
    const text = holder.innerText.trim();
    holder.remove();

    return { html, text };
  }

  /**
   * Fallback for contexts where the async Clipboard API is missing or refuses (plain http, older browsers).
   *
   * Selects the rendered HTML and runs the legacy copy command, which also puts rich text on the clipboard. The
   * holder is attached inside the modal (when there is one) because Bootstrap's focus trap would steal the
   * selection otherwise.
   */
  function legacyCopy(html, container) {
    const holder = document.createElement("div");
    holder.innerHTML = html;
    holder.style.position = "fixed";
    holder.style.left = "-9999px";
    holder.style.top = "0";
    holder.style.userSelect = "text";
    (container || document.body).appendChild(holder);
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(holder);
    selection.removeAllRanges();
    selection.addRange(range);
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } finally {
      selection.removeAllRanges();
      holder.remove();
    }
    if (!ok) throw new Error("Legacy copy command refused");
  }

  async function writeToClipboard(html, text, container) {
    try {
      if (!navigator.clipboard) throw new Error("Clipboard API not available");
      if (window.ClipboardItem) {
        await navigator.clipboard.write([
          new ClipboardItem({
            "text/html": new Blob([html], { type: "text/html" }),
            "text/plain": new Blob([text], { type: "text/plain" }),
          }),
        ]);
      } else {
        await navigator.clipboard.writeText(text);
      }
    } catch (e) {
      console.warn("Async clipboard copy failed, using the legacy copy command:", e);
      legacyCopy(html, container);
    }
  }

  function setState(button, copied) {
    const icon = button.querySelector("i.bi");
    const label = button.querySelector("[data-copy-label-text]");
    if (icon) {
      icon.classList.toggle("bi-clipboard", !copied);
      icon.classList.toggle("bi-clipboard-check", copied);
    }
    if (label) label.textContent = copied ? button.dataset.copiedLabel : button.dataset.copyLabel;
  }

  function showCopied(button) {
    clearTimeout(timers.get(button));
    setState(button, true);
    timers.set(
      button,
      setTimeout(() => setState(button, false), FEEDBACK_MS)
    );
  }

  async function copyFromSource(button) {
    const error = button.parentElement.querySelector("[data-copy-error]");
    if (error) error.hidden = true;
    try {
      const root = button.closest("[data-copy-root]");
      const template = root && root.querySelector(button.dataset.copySource);
      if (!template) throw new Error("Copy source not found");
      const { html, text } = extractContent(template);
      if (!text) throw new Error("Nothing to copy");
      await writeToClipboard(html, text, button.closest(".modal"));
      showCopied(button);
    } catch (e) {
      console.error("Copy failed:", e);
      clearTimeout(timers.get(button));
      setState(button, false);
      if (error) error.hidden = false;
    }
  }

  document.addEventListener("click", e => {
    const button = e.target.closest("[data-copy-source]");
    if (button) copyFromSource(button);
  });
})();
