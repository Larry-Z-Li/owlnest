// The photo slideshow on a property's page (see templates/properties/detail.html).
//
// The photos sit side by side in a strip that scrolls one photo at a time (CSS
// scroll-snap), so swiping works on a phone even without this file. This makes the
// arrow buttons work (after the last photo comes the first again), and keeps the
// "2 of 5" count up to date.

(function () {
  "use strict";

  var show = document.querySelector(".slideshow");
  var strip = show && show.querySelector(".slides");
  if (!strip) return;
  var number = show.querySelector(".slide-number");
  var count = strip.children.length;
  var stillMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Which photo is showing (0 for the first).
  function current() {
    return Math.round(strip.scrollLeft / strip.clientWidth);
  }

  function go(step) {
    var next = (current() + step + count) % count;
    strip.scrollTo({ left: next * strip.clientWidth, behavior: stillMotion ? "auto" : "smooth" });
  }

  show.querySelector(".slide-previous").addEventListener("click", function () {
    go(-1);
  });
  show.querySelector(".slide-next").addEventListener("click", function () {
    go(1);
  });
  strip.addEventListener(
    "scroll",
    function () {
      number.textContent = current() + 1;
    },
    { passive: true }
  );
})();
