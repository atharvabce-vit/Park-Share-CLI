(function () {
  "use strict";

  document.querySelectorAll('input[type="password"]').forEach(function (input) {
    var field = input.closest("label.field");
    if (!field) {
      return;
    }

    var toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "text-link password-toggle";
    toggle.textContent = "Show";
    toggle.setAttribute("aria-label", "Show password");
    toggle.setAttribute("aria-pressed", "false");
    toggle.addEventListener("click", function () {
      var isVisible = input.type === "text";
      input.type = isVisible ? "password" : "text";
      toggle.textContent = isVisible ? "Show" : "Hide";
      toggle.setAttribute("aria-label", isVisible ? "Show password" : "Hide password");
      toggle.setAttribute("aria-pressed", String(!isVisible));
    });
    field.insertAdjacentElement("afterend", toggle);
  });
}());
