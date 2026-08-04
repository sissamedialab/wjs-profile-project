document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector(".wjs-profile-form");
  if (!form) return;

  const modalElement = document.getElementById("unsavedChangesModal");
  if (!modalElement) return;

  const bootstrapModal = new bootstrap.Modal(modalElement);
  const confirmExitBtn = document.getElementById("confirmExitBtn");

  // Get initial form state to check if user clears fields
  function captureFormState() {
    const state = new Map();
    form.querySelectorAll("input, select, textarea").forEach(field => {
      if (!field.name) return;
      state.set(field, getFieldValue(field));
    });
    return state;
  }

  const initialState = captureFormState();

  function getFieldValue(field) {
    if (field.type === "checkbox" || field.type === "radio") {
      return field.checked;
    }
    if (field.tagName === "SELECT" && field.multiple) {
      return Array.from(field.selectedOptions)
        .map(option => option.value)
        .sort()
        .join(",");
    }
    const editor = getTinyMceEditor(field);
    if (editor) {
      return editor.getContent();
    }
    return field.value;
  }

  function getTinyMceEditor(field) {
    return field.tagName === "TEXTAREA" && typeof tinymce !== "undefined" && field.id ? tinymce.get(field.id) : null;
  }

  function hasFormChanged() {
    for (const [field, initialValue] of initialState) {
      if (getFieldValue(field) !== initialValue) return true;
    }
    return false;
  }

  let isFormEdited = false;
  let nextTargetUrl = null;

  const updateEditedState = () => {
    isFormEdited = hasFormChanged();
  };

  form.addEventListener("change", updateEditedState);
  form.addEventListener("input", updateEditedState);

  function bindTinyMceEditor(editor) {
    if (!form.contains(editor.getElement())) return;
    editor.on("input change keyup Undo Redo SetContent", updateEditedState);
  }

  if (typeof tinymce !== "undefined") {
    tinymce.editors.forEach(bindTinyMceEditor);
    tinymce.on("AddEditor", event => bindTinyMceEditor(event.editor));
  }

  form.addEventListener("submit", () => {
    isFormEdited = false;
  });

  window.addEventListener("beforeunload", event => {
    if (isFormEdited) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  document.addEventListener("click", event => {
    if (!isFormEdited) return;
    if (event.target.closest("#unsavedChangesModal")) return;

    const targetLink = event.target.closest("a");
    const targetButton = event.target.closest('button, input[type="button"]');

    if (targetLink) {
      const rawHref = targetLink.getAttribute("href");
      const isInPageAnchor = !rawHref || rawHref.startsWith("#") || rawHref.startsWith("javascript:");
      if (isInPageAnchor) return;

      event.preventDefault();
      nextTargetUrl = targetLink.href;
      bootstrapModal.show();
      return;
    }

    if (targetButton) {
      const isBootstrapToggle =
        targetButton.hasAttribute("data-bs-toggle") || targetButton.hasAttribute("data-bs-dismiss");
      if (isBootstrapToggle || targetButton.type === "submit" || targetButton.id === "toggleAllAccordions") return;

      event.preventDefault();
      nextTargetUrl = "back";
      bootstrapModal.show();
    }
  });

  confirmExitBtn.addEventListener("click", () => {
    isFormEdited = false;
    bootstrapModal.hide();

    if (nextTargetUrl === "back") {
      window.history.back();
    } else if (nextTargetUrl) {
      window.location.href = nextTargetUrl;
    }
  });
});
