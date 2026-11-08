// The status menus on the Maintenance list and on Home (see templates/_status.html).
//
// Picking a status saves it at once, without leaving the page. If the list doesn't
// show requests with the new status (such as Closed, on a list of open requests), the
// request fades away, and a message says where it went, with an Undo button.

(function () {
  "use strict";

  var UNDO_SECONDS = 10;
  var FADE_MS = 450;
  var CLOSE_GAP_MS = 350;
  var stillMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var DIDNT_SAVE = "That didn't save.";
  var toast = null;
  var toastTimer = null;

  document.addEventListener("change", function (event) {
    var menu = event.target;
    if (!menu.matches(".status-menu select")) return;
    var form = menu.form;
    var row = form.closest("tr");
    var table = row.closest("table");
    var title = row.querySelector("a").textContent.trim();
    var before = form.dataset.saved || savedOnPage(menu);
    setStatus(form, menu.value).then(
      function () {
        var shows = JSON.parse(table.dataset.shows || "[]");
        if (shows.indexOf(menu.value) === -1) {
          takeOff(row, table, function () {
            var undo = function () {
              putBack(row, table, form, before, title, undo);
            };
            showToast("“" + title + "” is now " + menu.value.toLowerCase() + ".", undo);
          });
        }
      },
      function () {
        showColor(form, before);
        menu.value = before;
        showToast(DIDNT_SAVE + " Please check the internet connection, then try again.", null, true);
      }
    );
  });

  // Saves the status. Resolves when it's saved; rejects if it couldn't be.
  function setStatus(form, status) {
    var was = form.dataset.saved || savedOnPage(form.querySelector("select"));
    var fields = new FormData(form);
    fields.set("status", status);
    fields.set("stay_on_page", "1");
    form.classList.add("saving");
    showColor(form, status);
    return fetch(form.action, { method: "POST", body: fields, redirect: "manual" }).then(function (answer) {
      form.classList.remove("saving");
      if (answer.type === "opaqueredirect") {
        // Signed out meanwhile (such as after 30 days): reloading shows the sign-in page.
        window.location.reload();
        return Promise.reject();
      }
      if (answer.status !== 204) return Promise.reject();
      form.dataset.saved = status;
      form.querySelector("select").value = status;
      updateCounts(was, status);
    }, function () {
      form.classList.remove("saving");
      return Promise.reject();
    });
  }

  // The counts in the Maintenance tabs: each lists the statuses it counts (data-counts).
  function updateCounts(from, to) {
    document.querySelectorAll("[data-counts]").forEach(function (count) {
      var statuses = JSON.parse(count.dataset.counts);
      var number = Number(count.textContent);
      if (statuses.indexOf(from) !== -1) number -= 1;
      if (statuses.indexOf(to) !== -1) number += 1;
      count.textContent = number;
    });
  }

  function showColor(form, status) {
    var pick = form.querySelector(".status-pick");
    var option = form.querySelector('option[value="' + status + '"]');
    form.querySelectorAll("option").forEach(function (each) {
      pick.classList.remove(each.dataset.color);
    });
    if (option) pick.classList.add(option.dataset.color);
  }

  // Fades the row away, closes the gap it leaves, then removes it.
  // (Timers, not the animations' own "finished", say when: the row goes even if the
  // browser skips drawing, such as in a tab in the background.)
  function takeOff(row, table, done) {
    row.dataset.place = Array.prototype.indexOf.call(row.parentNode.children, row);
    var animations = [];
    var finish = function () {
      row.remove();
      // Back to how it looked, for Undo. (Its animations are kept, not looked up: once the
      // row is off the page, the browser no longer lists them.)
      animations.forEach(function (animation) { animation.cancel(); });
      openGap(row);
      showEmptyList(table);
      done();
    };
    if (stillMotion) return finish();
    animations.push(row.animate(
      [{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateX(32px)" }],
      { duration: FADE_MS, easing: "ease-in", fill: "forwards" }
    ));
    setTimeout(function () {
      animations.push.apply(animations, closeGap(row));
      setTimeout(finish, CLOSE_GAP_MS);
    }, FADE_MS);
  }

  // Shrinks each cell of a faded row to nothing, so the rows below slide up. Returns the animations.
  function closeGap(row) {
    var animations = [];
    Array.prototype.forEach.call(row.cells, function (cell) {
      var style = getComputedStyle(cell);
      var inner = document.createElement("div");
      inner.className = "closing";
      while (cell.firstChild) inner.appendChild(cell.firstChild);
      cell.appendChild(inner);
      inner.style.height = cell.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom) + "px";
      animations.push(cell.animate(
        [
          { paddingTop: style.paddingTop, paddingBottom: style.paddingBottom, borderBottomWidth: style.borderBottomWidth },
          { paddingTop: "0px", paddingBottom: "0px", borderBottomWidth: "0px" },
        ],
        { duration: CLOSE_GAP_MS, easing: "ease-out", fill: "forwards" }
      ));
      animations.push(inner.animate([{ height: inner.style.height }, { height: "0px" }], {
        duration: CLOSE_GAP_MS,
        easing: "ease-out",
        fill: "forwards",
      }));
    });
    return animations;
  }

  // Undoes closeGap's wrapping of each cell's contents.
  function openGap(row) {
    Array.prototype.forEach.call(row.cells, function (cell) {
      var inner = cell.querySelector(":scope > .closing");
      if (!inner) return;
      while (inner.firstChild) cell.appendChild(inner.firstChild);
      inner.remove();
    });
  }

  // Undo: saves the status it had before, and puts the row back where it was.
  function putBack(row, table, form, status, title, undo) {
    setStatus(form, status).then(
      function () {
        var body = table.tBodies[0];
        body.insertBefore(row, body.children[Number(row.dataset.place)] || null);
        showEmptyList(table);
        if (!stillMotion) {
          row.animate([{ opacity: 0, backgroundColor: "#fff3cd" }, { opacity: 1, backgroundColor: "transparent" }], {
            duration: 900,
            easing: "ease-out",
          });
        }
        hideToast();
      },
      function () {
        showColor(form, form.dataset.saved);
        showToast(DIDNT_SAVE + " “" + title + "” is still " + form.dataset.saved.toLowerCase() + ". Please check the internet connection, then try Undo again.", undo, true);
      }
    );
  }

  // With no rows left, the list says so instead (and shows its rows again after Undo).
  function showEmptyList(table) {
    var empty = table.nextElementSibling;
    if (!empty || !empty.classList.contains("empty-now")) {
      empty = document.createElement("p");
      empty.className = (table.dataset.emptyClass || "empty") + " empty-now";
      empty.textContent = table.dataset.empty;
      table.after(empty);
    }
    var none = table.tBodies[0].rows.length === 0;
    table.hidden = none;
    empty.hidden = !none;
  }

  // A message at the bottom of the window, for a few seconds. undo, if given, is what its Undo button does.
  function showToast(text, undo, isProblem) {
    hideToast();
    toast = document.createElement("div");
    toast.className = "message message-with-button toast " + (isProblem ? "message-error" : "message-success");
    toast.setAttribute("role", "status");
    var words = document.createElement("span");
    words.textContent = text;
    toast.appendChild(words);
    if (undo) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "button button-small";
      button.textContent = "Undo";
      button.addEventListener("click", function () {
        button.disabled = true;
        undo();
      });
      toast.appendChild(button);
    }
    document.body.appendChild(toast);
    toastTimer = setTimeout(hideToast, UNDO_SECONDS * 1000);
  }

  function hideToast() {
    clearTimeout(toastTimer);
    if (toast) toast.remove();
    toast = null;
  }

  // The status the page came with (its "selected" option stays the same when she picks another).
  function savedOnPage(menu) {
    var option = menu.querySelector("option[selected]");
    return option ? option.value : menu.value;
  }
})();
