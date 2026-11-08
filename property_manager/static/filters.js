// The filter bars above the lists (Transactions, From the bank, Properties, Maintenance) and Reports.
//
// Picking from a menu shows the result at once, so the Show button goes away. (Pressing
// Enter in a search box still works, and without this file the Show button does it all.)
// On a phone, Transactions folds its menus away under "Filters", unless one is in use.

(function () {
  "use strict";

  document.querySelectorAll("form[data-auto-filter]").forEach(function (form) {
    var showButton = form.querySelector("[data-show-button]");
    if (showButton) showButton.hidden = true;

    var more = form.querySelector(".more-filters");
    if (more && !form.hasAttribute("data-active") && window.matchMedia("(max-width: 600px)").matches) {
      more.open = false;
    }

    form.addEventListener("change", function (event) {
      var field = event.target;
      if (field.type === "search") return; // (Enter searches.)
      // (A date part-way through being typed, such as the year 0002, waits until it's finished.)
      if (field.type === "date" && (!field.value || field.value.slice(0, 4) < "1900")) return;
      if (field.name === "period") {
        // "Pick dates…" waits for the dates.
        var dates = form.querySelector(".period-dates");
        dates.hidden = field.value !== "custom";
        if (field.value === "custom") return;
      }
      form.submit();
    });
  });
})();
