// 首頁篩選：依卡片上的 data-* 屬性（由 build.py 從 front matter 產生）顯示或隱藏咖啡廳。
(function () {
  var form = document.querySelector("[data-filter]");
  if (!form) return;
  var cards = Array.prototype.slice.call(document.querySelectorAll("[data-cafe]"));
  var count = document.querySelector("[data-count]");
  var empty = document.querySelector("[data-empty]");

  function apply() {
    var f = new FormData(form);
    var shown = 0;
    cards.forEach(function (card) {
      var d = card.dataset;
      var ok = true;
      if (f.get("outlets") && d.outlets !== "多" && d.outlets !== "部分") ok = false;
      if (f.get("wifi") && d.wifi !== "穩定") ok = false;
      if (f.get("quiet") && d.quiet !== "安靜") ok = false;
      if (f.get("late") && d.late !== "1") ok = false;
      if (f.get("nolimit") && d.nolimit !== "1") ok = false;
      if (f.get("station") && d.station !== f.get("station")) ok = false;
      if (f.get("tag") && d.tags.split(",").indexOf(f.get("tag")) === -1) ok = false;
      card.hidden = !ok;
      if (ok) shown++;
    });
    if (count) count.textContent = shown;
    if (empty) empty.hidden = shown !== 0;
  }

  form.addEventListener("change", apply);
  form.addEventListener("reset", function () { setTimeout(apply, 0); });
  apply();
})();
