// ── VND price inputs (spec: "20.000" must mean 20 nghìn, not 20) ──
// type="number" parses "." as a DECIMAL point, so a Vietnamese user typing
// "20.000" (their normal thousands-separator style) silently becomes 20 —
// which then fails "must be >= 8800" with no visible reason. Every VND price
// field must instead be a text input that displays dots as thousands
// separators but submits plain digits, so both the user's input and the
// server's float() parsing agree on the actual value.

function vndDigitsOnly(str) {
  return (str || '').replace(/[^\d]/g, '');
}

function vndFormat(str) {
  const digits = vndDigitsOnly(str).replace(/^0+(?=\d)/, '');
  if (!digits) return '';
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, '.');
}

// Raw integer value (no dots) for any .vnd-price-input — use this instead of
// parseFloat(input.value) anywhere that reads one of these fields in JS.
function vndValue(input) {
  return parseInt(vndDigitsOnly(input.value), 10) || 0;
}

document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('.vnd-price-input').forEach(function (input) {
    // Initial value is rendered server-side as plain digits (e.g. "20000") —
    // display it pre-formatted from the start.
    input.value = vndFormat(input.value);

    input.addEventListener('input', function () {
      const cursorFromEnd = this.value.length - (this.selectionStart || this.value.length);
      this.value = vndFormat(this.value);
      const pos = Math.max(0, this.value.length - cursorFromEnd);
      this.setSelectionRange(pos, pos);
    });

    // Submit plain digits, not the dot-formatted display string, so the
    // server's float()/Form parsing (and the "20.000" bug) never recurs.
    const form = input.closest('form');
    if (form && !form.dataset.vndSubmitWired) {
      form.dataset.vndSubmitWired = '1';
      form.addEventListener('submit', function () {
        form.querySelectorAll('.vnd-price-input').forEach(function (el) {
          el.value = vndDigitsOnly(el.value);
        });
      });
    }
  });
});
