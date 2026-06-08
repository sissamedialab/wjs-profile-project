document.addEventListener("DOMContentLoaded", function () {
  // Select the primary header and the low menu
  const header = document.querySelector(".header-main");

  // Exit early if the main header isn't found to prevent errors
  if (!header) return;

  // Configuration for scroll behavior
  const startThreshold = 75; // Scroll depth (px) to trigger the "scrolled" state
  const endThreshold = 1; // Scroll depth (px) to reset back to the "top" state
  let isScrolled = false; // Boolean flag to track the current state and prevent redundant DOM updates

  function handleHeaderScroll() {
    const scrollPos = window.scrollY;
    if (scrollPos > startThreshold && !isScrolled) {
      header.classList.add("is-scrolled");
      isScrolled = true;
    } else if (scrollPos < endThreshold && isScrolled) {
      header.classList.remove("is-scrolled");
      isScrolled = false;
    }
  }

  handleHeaderScroll();

  // Monitor scroll position to toggle header classes and ARIA states
  window.addEventListener("scroll", handleHeaderScroll);

  // Select elements that need to stay "stuck" right below the header
  const stickyTableHeaders = document.querySelectorAll("thead.sticky-top");
  const filterContainer = document.querySelector("#sticky-filter-container");

  /**
   * ResizeObserver handles dynamic layout shifts.
   * If the header height changes (e.g., due to the 'is-scrolled' class or window resizing),
   * this ensures the top offset of sticky elements is updated automatically.
   */
  const resizeObserver = new ResizeObserver(entries => {
    for (let entry of entries) {
      // Get the current height of the header (including padding/borders)
      const newHeight = header.offsetHeight;

      // Update the CSS 'top' property for all sticky table headers
      stickyTableHeaders.forEach(thead => {
        thead.style.top = newHeight + "px";
      });

      // Update the CSS 'top' property for the filter container
      if (filterContainer) {
        filterContainer.style.top = newHeight + "px";
      }

      document.documentElement.style.scrollPaddingTop = newHeight + "px";
    }
  });

  // Start watching the header for size changes
  resizeObserver.observe(header);

  document.documentElement.style.scrollPaddingTop = header.offsetHeight + "px";

  if (window.location.hash) {
    const target = document.querySelector(window.location.hash);
    if (target) {
      setTimeout(() => {
        target.scrollIntoView();
      }, 0);
    }
  }
});
