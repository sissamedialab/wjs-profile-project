/**
 * Check the visibility of the date input field based on the selected decision option.
 *
 * @param {Object} decision - The object containing decision data.
 * @param {Array} decision.selectedOptions - The array of selected options in the decision.
 * @param {Object} decision.selectedOptions[0] - The first selected option.
 * @param {string} decision.selectedOptions[0].value - The value of the first selected option.
 * @return {void} Does not return a value.
 */
function checkDateVisibility(decision) {
  if (["minor_revisions", "major_revisions", "tech_revisions"].indexOf(decision.selectedOptions[0].value) > -1) {
    document.querySelector(".date-due").classList.remove("visually-hidden");
  } else {
    document.querySelector(".date-due").classList.add("visually-hidden");
  }
}

/**
 * Copies the content of a reviewer's report from a modal and appends it to the decision editor field.
 *
 * @param {string} id - The identifier used to locate the specific modal containing the reviewer's report.
 * @return {void} - Does not return a value.
 */
function copyReviewerReport(id) {
  const text = document.querySelector(`#author_review_tex_modal-${id} .modal-body`)?.innerText.trim();
  if (!text) return;
  const editor = document.getElementById("id_decision_editor_report");
  if (!editor) return;
  if (editor.value) editor.value += "\n\n-------------------\n\n";
  editor.value += text;
}

/**
 * Copy the provided LaTeX content into the decision editor report.
 * If the editor already contains text, append the new content
 * separated by a delimiter line.
 *
 * @param {string} latexContent - The LaTeX content to be copied into the editor.
 * @return {void} - Does not return a value.
 */
function copyConvertedReport(latexContent) {
  if (!latexContent) return;
  const editor = document.getElementById("id_decision_editor_report");
  if (!editor) return;
  if (editor.value) editor.value += "\n\n-------------------\n\n";
  editor.value += latexContent;
}

document.addEventListener("DOMContentLoaded", function () {
  const decision = document.querySelector("#id_decision");

  decision.addEventListener("change", function () {
    checkDateVisibility(decision);
  });

  checkDateVisibility(decision);
});

document.addEventListener("click", e => {
  if (!e.target.classList.contains("copy-tex-btn")) return;
  copyReviewerReport(e.target.dataset.reviewId);
});

document.body.addEventListener("htmx:afterOnLoad", e => {
  if (!e.target.classList.contains("copy-tex-btn")) return;
  copyConvertedReport(e.detail.xhr.responseText.trim());
});
