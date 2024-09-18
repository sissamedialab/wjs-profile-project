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
  tinymce.triggerSave();
  // update the parameter in request
  richContent = document.querySelector(`#id_${field}`);
  event.detail.parameters[field] = richContent.value;
};
