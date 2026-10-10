// Reveals the "Skip to content" link only while the user is navigating by
// keyboard. WebView2 puts focus on the first tab stop when the PowerAtlas
// window is shown, which is this link, so a plain `:focus` rule made it pop up
// on every open for a mouse user.
//
// `html.kbd-nav` is set by a Tab keydown and cleared by a mouse press or by the
// window losing focus. style.css shows the link only under `html.kbd-nav`. When
// the link takes focus with no Tab behind it, focus is handed to <body> so the
// next Tab still lands on the link, and the link then appears.
(function () {
  var root = document.documentElement;

  function setKeyboard(on) {
    root.classList.toggle('kbd-nav', on);
  }

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Tab') setKeyboard(true);
  }, true);
  document.addEventListener('mousedown', function () { setKeyboard(false); }, true);
  window.addEventListener('blur', function () { setKeyboard(false); });

  document.addEventListener('focusin', function (e) {
    var el = e.target;
    if (!el || !el.classList || !el.classList.contains('skip-link')) return;
    if (root.classList.contains('kbd-nav')) return;
    var body = document.body;
    var had = body.hasAttribute('tabindex');
    if (!had) body.setAttribute('tabindex', '-1');
    body.focus();
    if (!had) body.removeAttribute('tabindex');
  }, true);
})();
