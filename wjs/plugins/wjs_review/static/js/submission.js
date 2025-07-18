// This script provides a dynamic checklist made of sections' heading,
//  that will be marked once the required fields will be filled

// Wait for the DOM to be fully loaded before running the script
document.addEventListener("DOMContentLoaded", function () {
  //  Retrieve the closest h3 inside a section where a required field is
  function getSectionHeading(field) {
    let el = field;
    while (el && el !== form) {
      let sibling = el.previousElementSibling;
      while (sibling) {
        if (sibling.tagName === "H3") {
          return sibling.textContent.trim();
        }
        sibling = sibling.previousElementSibling;
      }
      el = el.parentElement;
    }
  }

  //  - Checks if a required field is filled.
  //  - For radio groups, checks if any radio in the group is checked.
  //  - For checkboxes, checks if it is checked.
  //  - For selects, checks if a value is selected.
  //  - For other fields, checks if the value is non-empty.

  function isFilled(fields) {
    return fields.every(field => {
      if (field.type === "radio") {
        return !!form.querySelector(`input[type="radio"][name="${field.name}"]:checked`);
      }
      if (field.type === "checkbox") return field.checked;
      if (field.tagName === "SELECT") return field.value !== "";
      return field.value?.trim() !== "";
    });
  }

  // Get references to the form, submit button, and form footer
  const form = document.querySelector("form");
  const submitBtn = document.getElementById("submit-btn");
  const formFooter = document.getElementById("form-footer");

  // Create a dynamic checklist <ul> to display required sections
  let list = document.createElement("ul");
  list.id = "wjs-submission-form__fields-list";
  list.setAttribute("aria-live", "polite"); // For accessibility
  form.insertBefore(list, formFooter);

  const sectionMap = new Map();
  // Used to avoid duplicate radio group entries
  const radios = new Set();
  // Build the checklist: one entry per section
  const checklist = [];

  // Find all required fields in the form and build the checklist
  // Only add one entry per radio group (by name)

  form.querySelectorAll("[required]").forEach(field => {
    if (field.type === "radio") {
      if (radios.has(field.name)) return;
      radios.add(field.name);
    }
    const section = getSectionHeading(field);
    if (!sectionMap.has(section)) {
      sectionMap.set(section, []);
    }
    sectionMap.get(section).push(field);
  });

  sectionMap.forEach((fields, section) => {
    const li = document.createElement("li");
    li.textContent = section;
    li.dataset.section = section;
    list.appendChild(li);
    checklist.push({ section, fields, li });
  });

  let arxivIdListItem = Array.from(document.querySelectorAll("li[data-section]")).find(li =>
    li.dataset.section.toLowerCase().includes("arxiv")
  );

  //  - Updates the checklist UI and submit button state.
  //  - Adds a CSS class to filled fields.
  //  - Enables the submit button only if all required fields are filled.

  function update() {
    let allSectionsFilled = true;
    checklist.forEach(item => {
      const filled = isFilled(item.fields);
      if (filled) {
        item.li.classList.add("wjs-submission-form__label--filled");
        if (item.li === arxivIdListItem) {
          arxivIdListItem.classList.remove("wjs-submission-form__label--filled");
        }
      } else {
        item.li.classList.remove("wjs-submission-form__label--filled");
        allSectionsFilled = false;
      }
    });
    submitBtn.disabled = !allSectionsFilled;
  }

  // Attach event listeners to update the checklist when fields change
  checklist.forEach(item => {
    item.fields.forEach(field => {
      if (field.type === "radio") {
        form
          .querySelectorAll(`input[type="radio"][name="${field.name}"]`)
          .forEach(radio => radio.addEventListener("change", update));
      } else {
        ["input", "change"].forEach(event => field.addEventListener(event, update));
      }
    });
  });

  // Initial update to set the correct state on page load
  update();

  // Function to handle ArXiv validation, his button and his warning message toggle

  const arxivInput = document.getElementById("arxiv-id-input");
  const resultDiv = document.getElementById("js-arxiv-validation-result");
  const suggestionDiv = document.getElementById("js-arxiv-validation-suggestion");
  const arxivValidationBtn = document.getElementById("js-arxiv-validation-btn");
  const loaderDiv = document.getElementById("js-arxiv-validation-loader");

  const arxivInputWrapper = arxivInput.closest(".wjs-submission-form__arxiv-validation-wrapper");
  const resultDivWrapper = resultDiv.closest(".wjs-submission-form__arxiv-validation-messages-wrapper");

  arxivInputWrapper.parentNode.insertBefore(suggestionDiv, resultDivWrapper);

  arxivInput.addEventListener("input", function () {
    const regex = /^\d{4}\.\d{5}$/;
    const isValid = regex.test(arxivInput.value);

    suggestionDiv.classList.toggle("d-none", arxivInput.value === "" || isValid);
    arxivValidationBtn.disabled = !isValid;
  });

  arxivInput.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !arxivValidationBtn.disabled) {
      event.preventDefault(); // Prevent form submission if inside a form
      arxivValidationBtn.click();
    }
  });

  arxivValidationBtn.addEventListener("click", function () {
    loaderDiv.classList.remove("d-none");
    resultDiv.classList.add("d-none");
  });

  document.addEventListener("htmx:afterRequest", function (event) {
    loaderDiv.classList.add("d-none");
    resultDiv.classList.remove("d-none");

    const responseData = JSON.parse(event.detail.xhr.response);
    const isSuccess = responseData.status === "success";

    if (arxivIdListItem) {
      arxivIdListItem.classList.toggle("wjs-submission-form__label--filled", isSuccess);
    }

    resultDiv.classList.toggle("wjs-submission-form__arxiv-validation-result--success", isSuccess);
    resultDiv.classList.toggle("wjs-submission-form__arxiv-validation-result--error", !isSuccess);

    const iconHtml = isSuccess
      ? '<i class="bi bi-check-circle-fill"></i>'
      : '<i class="bi bi-exclamation-triangle-fill"></i>';

    resultDiv.innerHTML = `${iconHtml} ${responseData.message}`;

    resultDiv.focus();

    // Funtion to update hidden input with arxiv validation processing request

    if (!event.detail.elt || event.detail.elt.id !== arxivValidationBtn.id) return;

    let responseText = event.detail.xhr.responseText;
    let res;
    try {
      res = JSON.parse(responseText);
    } catch (err) {
      console.error("Invalid JSON from arXiv microservice:", err, responseText);
      return;
    }

    if (res.status === "success") {
      const hidden = document.getElementById("arxiv-article-id-input");
      hidden.value = res.article_id;
    } else {
      console.log("Error fetching article: " + res.message);
    }
  });

  // Function to add a fake checkbox and label for "Use of AI", still updating hidden data

  function addAICheckbox() {
    // Find the input field by name
    const aiInput = document.querySelector('input[name="Use of AI"]');
    const aiLabel = document.querySelector(`label[for="${aiInput.id}"]`);

    aiInput.setAttribute("aria-hidden", "true");
    aiInput.classList.add("d-none");
    aiLabel.setAttribute("aria-hidden", "true");
    aiLabel.classList.add("d-none");

    // Create the Bootstrap switch
    const switchAI = document.createElement("div");
    switchAI.className = "form-check form-switch mb-2";

    const switchAIInput = document.createElement("input");
    switchAIInput.className = "form-check-input";
    switchAIInput.type = "checkbox";
    switchAIInput.id = "ai-switch-checkbox";
    switchAIInput.setAttribute("role", "switch");

    const switchLabel = document.createElement("label");
    switchLabel.className = "form-check-label";
    switchLabel.setAttribute("for", switchAIInput.id);
    switchLabel.textContent = "Use of AI";

    switchAI.appendChild(switchAIInput);
    switchAI.appendChild(switchLabel);

    // Insert the switch before the label
    aiLabel.parentNode.insertBefore(switchAI, aiLabel);

    // Toggle input visibility when switch is toggled
    switchAIInput.addEventListener("change", function () {
      aiInput.checked = this.checked;
      if (this.checked) {
        aiInput.classList.remove("d-none");
      } else {
        aiInput.classList.add("d-none");
      }
    });
  }

  addAICheckbox();
});
