// Remembers your place on each page: how far down you were, and which months or folds were open.
// Coming back to the page puts you there again (Larry, 2026-11-05). It's kept for this browser tab
// only, and each address keeps its own, so a page with different menus picked starts fresh.
// A page opened at a particular spot (an address ending in #...) goes there instead, and a form
// sent back with problems stays at the top, where its message is.

(function () {
  "use strict";

  var store;
  try {
    store = window.sessionStorage;
    store.getItem("place-test");
  } catch (error) {
    return; // (Private browsing may not allow it. Then pages just open at the top.)
  }
  var key = "place:" + location.pathname + location.search;

  function folds() {
    return Array.prototype.slice.call(document.querySelectorAll("main details"));
  }

  // A fold's name: its heading (such as "November 2026"), or the start of its summary.
  function nameOf(fold) {
    var label = fold.querySelector("summary h2, summary h3") || fold.querySelector("summary");
    return label ? label.textContent.replace(/\s+/g, " ").trim().slice(0, 60) : "";
  }

  function save() {
    var open = {};
    folds().forEach(function (fold) {
      open[nameOf(fold)] = fold.open;
    });
    try {
      store.setItem(key, JSON.stringify({ y: window.scrollY, open: open }));
    } catch (error) {}
  }

  var saved = null;
  try {
    saved = JSON.parse(store.getItem(key) || "null");
  } catch (error) {}

  var problems = document.querySelector('[aria-invalid="true"], .message-error');
  if (saved) {
    folds().forEach(function (fold) {
      var name = nameOf(fold);
      if (Object.prototype.hasOwnProperty.call(saved.open || {}, name)) fold.open = saved.open[name];
    });
    // Only from the very top: if you've already scrolled, or the browser put you back itself (reloading,
    // or the Back button), your place wins and this does nothing.
    if (!location.hash && !problems && window.scrollY === 0 && saved.y > 0) {
      window.scrollTo(0, saved.y);
      var putAt = window.scrollY;
      // Pictures can make the page taller as they load: once they have, finish going there,
      // but only if you're still exactly where this put you.
      window.addEventListener("load", function () {
        if (window.scrollY === putAt && putAt !== saved.y) window.scrollTo(0, saved.y);
      });
    }
  }

  var waiting = null;
  window.addEventListener("scroll", function () {
    if (waiting) return;
    waiting = setTimeout(function () {
      waiting = null;
      save();
    }, 300);
  }, { passive: true });
  document.addEventListener("toggle", save, true); // (a fold opened or closed)
  window.addEventListener("pagehide", save);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") save();
  });
})();
