// A second slider above something wide (the report on Reports, the homes on Home).

(function () {
  "use strict";

  // Something wide is hard to slide across when its scroll bar is far below. This second one,
  // above it, moves with it. It only shows when what it's for is too wide to fit.
  document.querySelectorAll("[data-scrolls]").forEach(function (top) {
    var wide = document.getElementById(top.getAttribute("data-scrolls"));
    var inside = top.firstElementChild;

    function fit() {
      inside.style.width = wide.scrollWidth + "px";
      top.hidden = wide.scrollWidth <= wide.clientWidth + 1;
    }

    // Only the bar she's moving leads. If each answered the other, the one just told to follow
    // would push back on the one she's moving, a step behind, and stutter a trackpad's glide.
    var following = null;
    var doneFollowing;

    function keepInStep(leader, follower) {
      leader.addEventListener("scroll", function () {
        if (following === leader) return;
        following = follower;
        follower.scrollLeft = leader.scrollLeft;
        clearTimeout(doneFollowing);
        doneFollowing = setTimeout(function () { following = null; }, 150);
      });
    }

    keepInStep(top, wide);
    keepInStep(wide, top);
    window.addEventListener("resize", fit);
    if (document.fonts) document.fonts.ready.then(fit); // (The font can change how wide it is.)
    fit();
  });
})();
