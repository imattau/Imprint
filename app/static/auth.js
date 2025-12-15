(function() {
  const modalId = 'auth-modal';
  async function ensureModal() {
    let existing = document.getElementById(modalId);
    if (existing) return existing;
    const resp = await fetch('/auth/modal', {credentials: 'same-origin'});
    const html = await resp.text();
    const wrapper = document.createElement('div');
    wrapper.innerHTML = html;
    document.body.appendChild(wrapper.firstElementChild);
    return document.getElementById(modalId);
  }

  async function showModal() {
    const modal = await ensureModal();
    modal.classList.add('visible');
    document.body.classList.add('modal-open');
    const extSection = document.getElementById('extension-auth');
    if (extSection && !(window.nostr)) {
      extSection.classList.add('muted-section');
    }
  }

  function closeModal() {
    const modal = document.getElementById(modalId);
    if (modal) {
      modal.classList.remove('visible');
      document.body.classList.remove('modal-open');
    }
  }

  async function connectNip07() {
    if (!(window.nostr && window.nostr.getPublicKey)) {
      alert('NIP-07 browser extension not available. Try remote signer.');
      return;
    }
    try {
      const pubkey = await window.nostr.getPublicKey();
      const resp = await fetch('/auth/login/nip07', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'HX-Request': 'true'},
        credentials: 'same-origin',
        body: JSON.stringify({pubkey, duration: '1h'})
      });
      const html = await resp.text();
      if (resp.ok) {
        const target = document.querySelector('#auth-status');
        if (target) {
          target.outerHTML = html;
        }
        document.body.dispatchEvent(new CustomEvent('authChanged', {bubbles: true}));
        closeModal();
      } else {
        alert('Failed to connect browser signer');
      }
    } catch (err) {
      console.error(err);
      alert('Failed to connect browser signer');
    }
  }

  window.showAuthModal = showModal;
  window.closeAuthModal = closeModal;
  window.connectNip07 = connectNip07;
  document.body.addEventListener('openAuthModal', showModal);
  document.body.addEventListener('authChanged', () => {
    // Reload to refresh nav links (editor/settings/admin) after auth changes.
    window.location.reload();
  });
})();
