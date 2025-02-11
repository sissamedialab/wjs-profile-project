function EnableSubmitEnterButton(formId, submitButtonId) {
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(`#${formId} input, #${formId} select`).forEach(item => {
      item.addEventListener("keypress", function (event) {
        if (event.which === 13) {
          event.preventDefault();
          document.getElementById(submitButtonId).click();
        }
      });
    });
  });
}
