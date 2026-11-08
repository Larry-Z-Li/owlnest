// The Reports page: the little notes on the charts.

(function () {
  "use strict";

  // Pointing at a bar or slice (or tabbing to it) shows its amounts. Every number here is
  // also in the report, so nothing is lost on a phone or without this file.
  var note = document.createElement("div");
  note.className = "chart-tip";
  note.hidden = true;
  document.body.appendChild(note);

  function show(mark, x, y) {
    note.textContent = "";
    var lines = mark.getAttribute("data-tip-body").split("\n");
    var title = document.createElement("div");
    title.className = "chart-tip-title";
    title.textContent = mark.getAttribute("data-tip-title");
    note.appendChild(title);
    lines.forEach(function (line) {
      var row = document.createElement("div");
      row.textContent = line;
      note.appendChild(row);
    });
    note.hidden = false;
    var left = Math.min(x + 14, window.innerWidth - note.offsetWidth - 8);
    note.style.left = Math.max(8, left) + window.scrollX + "px";
    note.style.top = y + 14 + window.scrollY + "px";
  }

  document.addEventListener("pointermove", function (event) {
    var mark = event.target.closest && event.target.closest("[data-tip-title]");
    if (mark) show(mark, event.clientX, event.clientY);
    else note.hidden = true;
  });
  document.addEventListener("focusin", function (event) {
    if (event.target.matches("[data-tip-title]")) {
      var box = event.target.getBoundingClientRect();
      show(event.target, box.left + box.width / 2, box.top + box.height / 2);
    }
  });
  document.addEventListener("focusout", function () { note.hidden = true; });
})();
