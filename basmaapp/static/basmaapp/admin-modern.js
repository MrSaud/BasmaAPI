(function () {
  function toBoolIcon(cell, value) {
    var span = document.createElement('span');
    span.className = 'bool-indicator ' + (value ? 'true' : 'false');
    span.title = value ? 'True' : 'False';
    span.innerHTML = value ? '&#10003;' : '&#10005;';
    cell.textContent = '';
    cell.appendChild(span);
  }

  function applyBoolIcons() {
    var tables = document.querySelectorAll('table');
    if (!tables.length) return;
    tables.forEach(function (table) {
      var cells = table.querySelectorAll('tbody td');
      cells.forEach(function (cell) {
        if (cell.querySelector('.bool-indicator')) return;
        var raw = (cell.textContent || '').trim().toLowerCase();
        if (raw === 'true') {
          toBoolIcon(cell, true);
        } else if (raw === 'false') {
          toBoolIcon(cell, false);
        }
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', applyBoolIcons);
  } else {
    applyBoolIcons();
  }
})();
