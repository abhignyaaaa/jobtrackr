// Confirmation for destructive forms (CSP forbids inline handlers, so wire them here).
document.addEventListener("submit", function (e) {
  var form = e.target;
  var msg = form && form.getAttribute && form.getAttribute("data-confirm");
  if (msg && !window.confirm(msg)) e.preventDefault();
});
