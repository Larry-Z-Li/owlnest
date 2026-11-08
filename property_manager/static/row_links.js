// Clicking anywhere on a row of a list opens it, as if its main link was clicked.
//
// This isn't done with an invisible part of the link stretched over the row, because
// some versions of Safari stretch that over the whole window instead of the row, so
// that clicking anywhere near the top of the page opened the list's last row.
document.addEventListener("click", function (event) {
  var row = event.target.closest("table.row-links tbody tr");
  if (!row || event.target.closest("a, button, input, select, textarea, label, summary, form")) return;
  if (String(window.getSelection())) return; // (she was selecting some words)
  var link = row.querySelector("a.row-link");
  if (link) {
    // (Keeps the keys held down, so Command-click still opens a new tab.)
    link.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, metaKey: event.metaKey, ctrlKey: event.ctrlKey, shiftKey: event.shiftKey }));
  }
});
