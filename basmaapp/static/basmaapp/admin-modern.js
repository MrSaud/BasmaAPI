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

  var docListenersBound = false;

  function closeComboboxPanel(wrapper) {
    if (!wrapper) return;
    var panel = wrapper.querySelector('.select-combobox-panel');
    var trigger = wrapper.querySelector('.select-combobox-trigger');
    var searchInput = wrapper.querySelector('.select-combobox-search');
    if (panel) {
      panel.hidden = true;
      panel.style.display = 'none';
    }
    if (trigger) trigger.setAttribute('aria-expanded', 'false');
    if (searchInput) searchInput.value = '';
  }

  function bindGlobalComboboxListeners() {
    if (docListenersBound) return;
    docListenersBound = true;

    document.addEventListener(
      'click',
      function (e) {
        document.querySelectorAll('.select-combobox').forEach(function (wrap) {
          if (wrap.contains(e.target)) return;
          closeComboboxPanel(wrap);
        });
      },
      true
    );

    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      document.querySelectorAll('.select-combobox-panel').forEach(function (panel) {
        if (!panel.hidden) {
          closeComboboxPanel(panel.closest('.select-combobox'));
        }
      });
    });
  }

  function getSearchPlaceholder() {
    var meta = document.querySelector('meta[name="select-search-placeholder"]');
    var fromMeta = meta && meta.getAttribute('content');
    if (fromMeta) return fromMeta;
    if (document.body && document.body.dataset && document.body.dataset.selectSearchPlaceholder) {
      return document.body.dataset.selectSearchPlaceholder;
    }
    return 'Search...';
  }

  function shouldEnhanceSelect(selectEl) {
    if (!selectEl || selectEl.tagName !== 'SELECT') return false;
    if (selectEl.closest('nav.admin-sidebar')) return false;
    if (selectEl.closest('.select-combobox')) return false;
    if (selectEl.hasAttribute('data-no-search')) return false;
    if (selectEl.multiple) return false;
    var sz = parseInt(selectEl.getAttribute('size') || '0', 10);
    if (sz > 1) return false;
    if (selectEl.disabled) return false;
    if (selectEl.getAttribute('data-searchable-select') === '0') return false;
    if (selectEl.dataset.searchableSelectReady === '1') return false;
    return true;
  }

  function snapshotOptions(selectEl) {
    return Array.from(selectEl.options).map(function (opt) {
      return {
        value: opt.value,
        text: opt.text,
        disabled: opt.disabled,
      };
    });
  }

  function enhanceSelect(selectEl) {
    selectEl.dataset.searchableSelectReady = '1';
    var originalOptions = snapshotOptions(selectEl);
    var searchPh = getSearchPlaceholder();

    var wrapper = document.createElement('div');
    wrapper.className = 'select-combobox';

    selectEl.parentNode.insertBefore(wrapper, selectEl);
    wrapper.appendChild(selectEl);

    selectEl.classList.add('select-combobox-native');
    selectEl.setAttribute('tabindex', '-1');

    var trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'select-combobox-trigger';
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');

    var panel = document.createElement('div');
    panel.className = 'select-combobox-panel';
    panel.hidden = true;
    panel.style.display = 'none';

    var searchInput = document.createElement('input');
    searchInput.type = 'text';
    searchInput.className = 'select-combobox-search';
    searchInput.setAttribute('autocomplete', 'off');
    searchInput.setAttribute('spellcheck', 'false');
    searchInput.setAttribute('aria-label', searchPh);
    searchInput.placeholder = searchPh;

    var listEl = document.createElement('div');
    listEl.className = 'select-combobox-list';

    panel.appendChild(searchInput);
    panel.appendChild(listEl);
    wrapper.appendChild(trigger);
    wrapper.appendChild(panel);

    function selectedLabel() {
      var opt = selectEl.options[selectEl.selectedIndex];
      return opt ? opt.text : '';
    }

    function syncTrigger() {
      var t = selectedLabel();
      trigger.textContent = t || '\u00a0';
    }

    function renderOptions(query) {
      var q = (query || '').trim().toLowerCase();
      listEl.innerHTML = '';

      originalOptions.forEach(function (item) {
        var text = (item.text || '').toLowerCase();
        var isEmptyVal = item.value === '';
        var matches = isEmptyVal || !q || text.indexOf(q) !== -1;
        if (!matches) return;

        if (item.disabled) {
          var disabledRow = document.createElement('div');
          disabledRow.className = 'select-combobox-option select-combobox-option--disabled';
          disabledRow.textContent = item.text;
          listEl.appendChild(disabledRow);
          return;
        }

        var row = document.createElement('button');
        row.type = 'button';
        row.className = 'select-combobox-option';
        row.setAttribute('role', 'option');
        row.dataset.value = item.value;
        row.textContent = item.text;
        if (item.value === selectEl.value) {
          row.setAttribute('aria-selected', 'true');
          row.classList.add('is-selected');
        }

        row.addEventListener('click', function (e) {
          e.preventDefault();
          e.stopPropagation();
          selectEl.value = item.value;
          selectEl.dispatchEvent(new Event('change', { bubbles: true }));
          syncTrigger();
          closeComboboxPanel(wrapper);
        });

        listEl.appendChild(row);
      });
    }

    function openPanel() {
      panel.hidden = false;
      panel.style.display = 'block';
      trigger.setAttribute('aria-expanded', 'true');
      searchInput.value = '';
      renderOptions('');
      window.requestAnimationFrame(function () {
        searchInput.focus();
      });
    }

    trigger.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (panel.hidden || panel.style.display === 'none') {
        document.querySelectorAll('.select-combobox-panel').forEach(function (p) {
          if (!p.hidden) closeComboboxPanel(p.closest('.select-combobox'));
        });
        openPanel();
      } else {
        closeComboboxPanel(wrapper);
      }
    });

    searchInput.addEventListener('click', function (e) {
      e.stopPropagation();
    });

    searchInput.addEventListener('input', function () {
      renderOptions(searchInput.value);
    });

    searchInput.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        e.stopPropagation();
        closeComboboxPanel(wrapper);
        trigger.focus();
      }
    });

    syncTrigger();
    selectEl.addEventListener('change', syncTrigger);

    var sid = selectEl.id;
    if (sid) {
      try {
        var lbl = null;
        if (typeof CSS !== 'undefined' && CSS.escape) {
          lbl = document.querySelector('label[for="' + CSS.escape(sid) + '"]');
        } else {
          lbl = document.querySelector(
            'label[for="' + String(sid).replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"]'
          );
        }
        if (lbl) {
          lbl.addEventListener('click', function (e) {
            e.preventDefault();
            trigger.focus();
            openPanel();
          });
        }
      } catch (err) {}
    }
  }

  function initSearchableSelects() {
    bindGlobalComboboxListeners();
    Array.prototype.forEach.call(document.querySelectorAll('select'), function (sel) {
      if (!shouldEnhanceSelect(sel)) return;
      enhanceSelect(sel);
    });
  }

  function boot() {
    applyBoolIcons();
    initSearchableSelects();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
