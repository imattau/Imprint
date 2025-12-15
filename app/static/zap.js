(function () {
  function setAmount(target) {
    const form = target.closest('form[data-zap-form]');
    if (!form) return;
    const input = form.querySelector('input[name="amount"]');
    if (!input) return;
    input.value = target.dataset.zapAmount;
  }

  async function handleZapSubmit(event) {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (!form.matches('form[data-zap-form]')) return;
    const signMode = form.dataset.signMode;
    if (signMode !== 'nip07' || form.dataset.signed === '1') {
        return;
    }
    if (!(window.nostr && window.nostr.signEvent)) {
      alert('Browser signer not available. Please connect a NIP-07 extension.');
      event.preventDefault();
      return;
    }
    event.preventDefault();
    try {
      const templateField = form.querySelector('input[name="event_template"]');
      const signedField = form.querySelector('input[name="signed_event"]');
      const template = JSON.parse(templateField.value);
      const signed = await window.nostr.signEvent(template);
      signedField.value = JSON.stringify(signed);
      form.dataset.signed = '1';
      htmx.trigger(form, 'submit');
    } catch (err) {
      console.error('Failed to sign zap request', err);
      alert('Could not sign zap request.');
    }
  }

  function handleCopy(target) {
    const text = target.dataset.copy;
    if (!text) return;
    navigator.clipboard?.writeText(text).catch(() => {});
    target.textContent = 'Copied';
    setTimeout(() => target.textContent = 'Copy', 1200);
  }

  document.addEventListener('click', (event) => {
    const preset = event.target.closest('[data-zap-amount]');
    if (preset) {
      event.preventDefault();
      setAmount(preset);
      return;
    }
    const copyBtn = event.target.closest('[data-copy]');
    if (copyBtn) {
      event.preventDefault();
      handleCopy(copyBtn);
    }
  });

  document.addEventListener('submit', (event) => {
    handleZapSubmit(event);
  });
})();
