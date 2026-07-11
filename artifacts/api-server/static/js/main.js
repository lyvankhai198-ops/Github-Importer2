// ── Theme ──
(function() {
  const saved = localStorage.getItem('theme') || 'light';
  document.documentElement.setAttribute('data-theme', saved);
})();

function setTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('theme', theme);
  const icon = document.querySelector('#themeToggle i, #themeToggleLogin i');
  if (icon) icon.className = theme === 'dark' ? 'bi bi-sun-fill' : 'bi bi-moon-fill';
}

document.addEventListener('DOMContentLoaded', function() {
  // Apply saved theme icon
  const saved = localStorage.getItem('theme') || 'light';
  const themeIcons = document.querySelectorAll('#themeToggle i, #themeToggleLogin i');
  themeIcons.forEach(icon => { icon.className = saved === 'dark' ? 'bi bi-sun-fill' : 'bi bi-moon-fill'; });

  // Theme toggle
  const themeBtn = document.getElementById('themeToggle');
  if (themeBtn) {
    themeBtn.addEventListener('click', function() {
      const current = document.documentElement.getAttribute('data-theme');
      setTheme(current === 'dark' ? 'light' : 'dark');
    });
  }
  const themeBtnLogin = document.getElementById('themeToggleLogin');
  if (themeBtnLogin) {
    themeBtnLogin.addEventListener('click', function() {
      const current = document.documentElement.getAttribute('data-theme');
      setTheme(current === 'dark' ? 'light' : 'dark');
      this.innerHTML = current === 'dark' ? '<i class="bi bi-moon-fill"></i> Chế độ tối' : '<i class="bi bi-sun-fill"></i> Chế độ sáng';
    });
  }

  // Sidebar toggle
  const sidebarToggle = document.getElementById('sidebarToggle');
  const closeSidebar = document.getElementById('closeSidebar');
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebarOverlay');
  function openSidebar() {
    sidebar && sidebar.classList.add('open');
    overlay && overlay.classList.add('active');
  }
  function closeSidebarFn() {
    sidebar && sidebar.classList.remove('open');
    overlay && overlay.classList.remove('active');
  }
  sidebarToggle && sidebarToggle.addEventListener('click', openSidebar);
  closeSidebar && closeSidebar.addEventListener('click', closeSidebarFn);
  overlay && overlay.addEventListener('click', closeSidebarFn);

  // Password show/hide
  const togglePassword = document.getElementById('togglePassword');
  if (togglePassword) {
    togglePassword.addEventListener('click', function() {
      const input = document.getElementById('passwordInput');
      const icon = document.getElementById('eyeIcon');
      if (input.type === 'password') {
        input.type = 'text';
        icon.className = 'bi bi-eye-slash';
      } else {
        input.type = 'password';
        icon.className = 'bi bi-eye';
      }
    });
  }

  // Flash message auto-dismiss
  setTimeout(function() {
    document.querySelectorAll('.flash-msg').forEach(function(el) {
      const bsAlert = bootstrap.Alert.getOrCreateInstance(el);
      bsAlert && bsAlert.close();
    });
  }, 4000);

  // Image preview for file inputs
  document.querySelectorAll('input[type="file"][accept*="image"]').forEach(function(input) {
    input.addEventListener('change', function() {
      const previewId = this.id + 'Preview';
      let preview = document.getElementById(previewId);
      if (!preview) {
        preview = this.closest('.mb-3')?.querySelector('img');
      }
      if (preview && this.files && this.files[0]) {
        preview.src = URL.createObjectURL(this.files[0]);
        preview.style.display = 'block';
      }
    });
  });

  // Bot status polling
  function pollBotStatus() {
    fetch('/api/bot-status')
      .then(r => r.json())
      .then(data => {
        const badge = document.getElementById('botStatusBadge');
        if (badge) {
          badge.textContent = 'Bot: ' + data.status;
          badge.className = 'bot-status-badge status-' + data.status;
        }
        const statusText = document.getElementById('botStatusText');
        if (statusText) {
          statusText.textContent = data.status;
          statusText.className = 'status-badge status-' + data.status;
        }
      })
      .catch(() => {});
  }
  pollBotStatus();
  setInterval(pollBotStatus, 5000);

  // Confirm delete for destructive actions
  document.querySelectorAll('[data-confirm]').forEach(function(el) {
    el.addEventListener('click', function(e) {
      if (!confirm(this.dataset.confirm || 'Bạn có chắc chắn?')) {
        e.preventDefault();
        e.stopPropagation();
      }
    });
  });
});
