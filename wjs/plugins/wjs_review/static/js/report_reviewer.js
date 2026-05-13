document.addEventListener("DOMContentLoaded", function () {
  const recommendationField = document.querySelector("#id_recommendation");
  const followUpField = document.querySelector("#id_follow_up_action");
  const suggestedReviewersField = document.querySelector("#id_suggested_reviewers");

  function updateFormVisibility() {
    const recommendationValue = recommendationField.value;

    if (recommendationValue === "revise_minor" || recommendationValue === "revise_major") {
      followUpField.style.display = "block";
      followUpField.parentElement.querySelector("label").style.display = "block";
    } else {
      followUpField.style.display = "none";
      followUpField.parentElement.querySelector("label").style.display = "none";
      followUpField.value = "";
    }

    if (followUpField.value === "another_reviewer") {
      suggestedReviewersField.style.display = "block";
      suggestedReviewersField.parentElement.querySelector("label").style.display = "block";
    } else {
      suggestedReviewersField.style.display = "none";
      suggestedReviewersField.parentElement.querySelector("label").style.display = "none";
    }
  }
  updateFormVisibility();
  if (recommendationField) {
    recommendationField.addEventListener("change", updateFormVisibility);
  }
  if (followUpField) {
    followUpField.addEventListener("change", updateFormVisibility);
  }
});

document.addEventListener("DOMContentLoaded", function () {
  const conflictRadios = document.querySelectorAll(`input[name=${conflictOfInterestName}]`);
  const submitButton = document.getElementById("submit-btn");
  const conflictMessage = document.getElementById("conflict-message");

  console.log("conflictRadios", conflictRadios);
  function toggleConflict(radio) {
    const isConflict = radio.value === "yes";
    submitButton.disabled = isConflict;
    conflictMessage.style.display = isConflict ? "block" : "none";
  }

  conflictRadios.forEach(function (radio) {
    radio.addEventListener("change", function () {
      toggleConflict(radio);
    });

    if (radio.checked) {
      toggleConflict(radio);
    }
  });
});

document.addEventListener("DOMContentLoaded", function () {
  const texRadio = document.getElementById("id_review_choice_0");
  const richTextRadio = document.getElementById("id_review_choice_1");
  const texField = document.getElementById("field_author_review_tex");
  const richTextField = document.getElementById("field_author_review");

  function toggleFields() {
    console.log("toggleFields", reportMode, texField);
    if (reportMode === "tex") {
      texField.style.display = "block";
      if (richTextField) richTextField.style.display = "none";
      return;
    }
    if (reportMode === "text") {
      richTextField.style.display = "block";
      if (texField) texField.style.display = "none";
      return;
    }
    if (reportMode === "tex+text") {
      texField.style.display = texRadio.checked ? "block" : "none";
      richTextField.style.display = richTextRadio.checked ? "block" : "none";
    }
  }

  toggleFields();
  texRadio?.addEventListener("change", toggleFields);
  richTextRadio?.addEventListener("change", toggleFields);
  if (reportModeSelected === "tex") {
    texRadio.checked = true;
    toggleFields();
  } else if (reportModeSelected === "rich_text") {
    richTextRadio.checked = true;
    toggleFields();
  }
});
