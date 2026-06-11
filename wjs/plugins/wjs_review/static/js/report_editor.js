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
 * @param {int} reviewOrder - The order of the report.
 * @return {void} - Does not return a value.
 */
function copyReviewerReport(id, reviewOrder) {
  const src = document.querySelector(
    `#author_review_tex_modal-${id} .modal-body, #author_review_modal-${id} .modal-body`
  );
  if (!src) return;

  /* innerText globs away newlines (it behaves like textContent) if the element is not visible, */
  /* because there's no rendered layout to read from. */
  /* Clone the src element, make it visible and park it off-screen before calling innerText */
  const clone = src.cloneNode(true);
  clone.style.position = "absolute";
  clone.style.left = "-9999px";
  clone.style.top = "0";
  clone.style.display = "block";
  document.body.appendChild(clone);
  const text = clone.innerText.trim();
  clone.remove();

  if (!text) return;
  const editor = document.getElementById("id_decision_editor_report");
  if (!editor) return;
  let content = `Report ${reviewOrder}\n\n${text}`;
  if (editor.value) content = `\n\n-------------------\n\n${content}`;
  editor.value += content;
}

/**
 * Copy the provided LaTeX content into the decision editor report.
 * If the editor already contains text, append the new content
 * separated by a delimiter line.
 *
 * @param {string} latexContent - The LaTeX content to be copied into the editor.
 * @param {int} reviewOrder - The order of the report.
 * @return {void} - Does not return a value.
 */
function copyConvertedReport(latexContent, reviewOrder) {
  if (!latexContent) return;
  const editor = document.getElementById("id_decision_editor_report");
  if (!editor) return;
  let content = `Report ${reviewOrder}\n\n${latexContent}`;
  if (editor.value) content = `\n\n-------------------\n\n${content}`;
  editor.value += content;
}

document.addEventListener("DOMContentLoaded", function () {
  const decision = document.querySelector("#id_decision");

  decision.addEventListener("change", function () {
    checkDateVisibility(decision);
  });

  checkDateVisibility(decision);
});

document.addEventListener("click", e => {
  if (!e.target.classList.contains("js-copy-tex-btn")) return;
  copyReviewerReport(e.target.dataset.reviewId, e.target.dataset.reviewOrder);
});

document.body.addEventListener("htmx:afterOnLoad", e => {
  if (!e.target.classList.contains("js-copy-tex-btn")) return;
  copyConvertedReport(e.detail.xhr.responseText.trim(), e.target.dataset.reviewOrder);
});
