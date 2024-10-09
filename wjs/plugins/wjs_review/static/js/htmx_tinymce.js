/**
 * When sending a tinymce form using htmx, the form data is not updated when the form is submitted.
 *
 * We must manually update the form data before the form is submitted by saving the tinymce content to the textarea and
 * copying the content to the htmx request parameters.
 *
 * It myst be called with:
 *
 * document.addEventListener("htmx:configRequest", function(event) {
 *    bindTinymce(event, <form-name>);
 * });
 *
 * See https://stackoverflow.com/a/70098713 for more information.
 *
 * @param event htmx:configRequest event
 * @param field django field name to update on form submit
 */
const bindTinymce = (event, field) => {
  tinymce.triggerSave(); // Save TinyMCE content to the textarea

  let richContent = document.querySelector(`#id_${field}`);

  if (richContent) {
    event.detail.parameters[field] = richContent.value;
  } else {
    console.error(`Element with ID #id_${field} not found in the DOM.`);
  }
};

/**
 * Cleanup tinymce and modal content when the modal is closed.
 *
 * This must be called when the modal is closed to ensure that the modal content is removed from the DOM to ensure
 * that form is initialized correctly when the modal is opened again.
 */
const unloadModal = () => {
  if (tinyMCE && tinyMCE.get("id_message")) tinyMCE.get("id_message").remove();

  const modalContent = document.getElementById("htmxModalContent");
  if (modalContent) {
    modalContent.innerHTML = "";
  }
  document.getElementById("htmxModal").removeEventListener("hidden.bs.modal", unloadModal);
};
