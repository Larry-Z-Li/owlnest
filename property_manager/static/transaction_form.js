// The transaction form (see templates/transactions/form.html).
//
// A transaction linked to a maintenance request takes on the request's property and
// type (its purpose). So while a request is chosen, the Property and Purpose menus
// show the request's and are greyed out. Choosing "— None —" for the request lets
// her pick them again.
//
// A transaction for a property is that property's LLC's, so while a property is chosen, the
// LLC menu shows its LLC and is greyed out. Without a property, she can choose the LLC itself.

(function () {
  "use strict";

  var requestMenu = document.getElementById("request_id");
  var propertyMenu = document.getElementById("property_id");
  var purposeMenu = document.getElementById("category");
  // Each request's property and type, by its id, such as {"3": {"property_id": 1, "type": "Landscaping"}}.
  var requests = JSON.parse(document.getElementById("requests-by-id").textContent);
  var llcMenu = document.getElementById("llc_id"); // (Not there until she has an LLC.)
  var llcByProperty = JSON.parse(document.getElementById("llc-by-property").textContent);

  function followProperty() {
    if (!llcMenu) return;
    if (propertyMenu.value) llcMenu.value = llcByProperty[propertyMenu.value] || "";
    llcMenu.disabled = Boolean(propertyMenu.value);
  }

  function followRequest() {
    var linked = requests[requestMenu.value];
    if (linked) {
      propertyMenu.value = linked.property_id;
      purposeMenu.value = linked.type;
    }
    propertyMenu.disabled = Boolean(linked);
    purposeMenu.disabled = Boolean(linked);
    followProperty();
  }

  requestMenu.addEventListener("change", followRequest);
  propertyMenu.addEventListener("change", followProperty);
  followRequest(); // in case the browser filled in the menus itself, such as after "Back"
})();
