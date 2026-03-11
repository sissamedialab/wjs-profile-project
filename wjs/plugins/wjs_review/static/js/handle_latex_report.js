/**
 * Log the details of an error event to the console.
 * @param {Object} event - The error event object.
 * @return {void} No return value.
 */
function logError(event) {
  console.log(event.detail);
}

/**
 * Clears the error state if the associated XMLHttpRequest completes successfully.
 *
 * Called by hx-on attribute on "Test PDF" button.
 *
 * @param {Object} event - The event object containing the XMLHttpRequest details.
 * @param {Object} event.detail - The detailed data from the event.
 * @param {XMLHttpRequest} event.detail.xhr - The XMLHttpRequest instance to check for success.
 * @return {void} This method does not return a value.
 */
function clearErrorOnSuccess(event) {
  const xhr = event.detail.xhr;
  if (xhr.status >= 200 && xhr.status < 300) {
    clearError();
  }
}

/**
 * Clear the error message displayed in the UI and hide the error element.
 *
 * Called by hx-on attribute on "Test PDF" button.
 *
 * @return {void} No return value.
 */
function clearError() {
  document.getElementById("pdf_error").innerText = "";
  document.getElementById("pdf_error").classList.add("d-none");
}

/**
 * Update the PDF error element with the provided error message and make it visible.
 *
 * Called by HX-Trigger payload from ElaborateLatexEditorReportView.
 *
 * @param {string} text - Error message to display.
 * @return {void} This method does not return a value.
 */
function setPdfError(text) {
  document.getElementById("pdf_error").innerHTML = text;
  document.getElementById("pdf_error").classList.remove("d-none");
}

document.body.addEventListener("yakunin-error", event => {
  setPdfError(event.detail.value);
});
