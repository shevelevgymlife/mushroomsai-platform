const messagesEl = document.getElementById('messages');
const inputEl = document.getElementById('messageInput');
const sendBtn = document.getElementById('sendBtn');
let isLoading = false;

document.addEventListener('DOMContentLoaded', function () {
  var c = document.getElementById('chatContainer');
  if (c) c.scrollTop = c.scrollHeight;
});

function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

function appendMessage(role, text) {
  const wrapper = document.createElement('div');
  wrapper.className = `chat-message flex gap-3 ${role === 'user' ? 'justify-end' : ''}`;

  if (role === 'assistant') {
    wrapper.innerHTML = `
      <div class="w-8 h-8 rounded-full bg-gradient-to-br from-yellow-600 to-yellow-400 flex-shrink-0 flex items-center justify-center">
        <span class="text-black font-bold text-xs">AI</span>
      </div>
      <div class="bg-[#111] border border-[#222] rounded-2xl rounded-tl-sm px-4 py-3" style="max-width:min(100%,28rem)">
        <p class="text-sm text-[#e8e8e8] leading-relaxed whitespace-pre-wrap">${escapeHtml(text)}</p>
      </div>`;
  } else {
    wrapper.innerHTML = `
      <div class="max-w-sm px-4 py-3 rounded-2xl rounded-tr-sm bg-[#3dd4e0]/10 border border-[#3dd4e0]/25">
        <p class="text-sm text-[#e8e8e8] leading-relaxed whitespace-pre-wrap">${escapeHtml(text)}</p>
      </div>`;
  }

  messagesEl.appendChild(wrapper);
  scrollBottom();
}

function appendLoader() {
  const el = document.createElement('div');
  el.id = 'loader';
  el.className = 'chat-message flex gap-3';
  el.innerHTML = `
    <div class="w-8 h-8 rounded-full bg-gradient-to-br from-yellow-600 to-yellow-400 flex-shrink-0 flex items-center justify-center">
      <span class="text-black font-bold text-xs">AI</span>
    </div>
    <div class="bg-[#111] border border-[#222] rounded-2xl rounded-tl-sm px-4 py-3" style="max-width:min(100%,28rem)">
      <div class="flex gap-1.5 items-center h-5">
        <span class="w-1.5 h-1.5 rounded-full bg-[#888] animate-bounce" style="animation-delay:0ms"></span>
        <span class="w-1.5 h-1.5 rounded-full bg-[#888] animate-bounce" style="animation-delay:150ms"></span>
        <span class="w-1.5 h-1.5 rounded-full bg-[#888] animate-bounce" style="animation-delay:300ms"></span>
      </div>
    </div>`;
  messagesEl.appendChild(el);
  scrollBottom();
}

function removeLoader() {
  const el = document.getElementById('loader');
  if (el) el.remove();
}

function scrollBottom() {
  const container = document.getElementById('chatContainer');
  container.scrollTop = container.scrollHeight;
}

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

async function sendMessage() {
  if (isLoading) return;
  const text = inputEl.value.trim();
  if (!text) return;

  inputEl.value = '';
  inputEl.style.height = 'auto';
  isLoading = true;
  sendBtn.disabled = true;
  sendBtn.classList.add('opacity-50');

  appendMessage('user', text);
  appendLoader();

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ message: text }),
    });

    removeLoader();

    if (res.status === 429) {
      const data = await res.json();
      appendMessage('assistant', data.message || 'Лимит исчерпан. Подключите подписку для продолжения.');
    } else if (!res.ok) {
      appendMessage('assistant', 'Произошла ошибка. Попробуйте ещё раз.');
    } else {
      const data = await res.json();
      appendMessage('assistant', data.answer);
    }
  } catch (e) {
    removeLoader();
    appendMessage('assistant', 'Ошибка соединения. Проверьте интернет и попробуйте снова.');
  } finally {
    isLoading = false;
    sendBtn.disabled = false;
    sendBtn.classList.remove('opacity-50');
    inputEl.focus();
  }
}

// Auto-resize textarea
inputEl.addEventListener('input', function () {
  this.style.height = 'auto';
  this.style.height = Math.min(this.scrollHeight, 120) + 'px';
});
