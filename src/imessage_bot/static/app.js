const $ = (id) => document.getElementById(id);
const state = { chats: [], selected: null, messages: [], status: null, filter: 'all', query: '', hasMore: false, loading: false, dirty: false, request: 0 };
let toastTimer;
const timeFormat = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' });
const dayFormat = new Intl.DateTimeFormat(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
const busyStages = ['exporting', 'preparing', 'training', 'validating'];

function duration(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return 'Estimating…';
  const minutes = Math.ceil(Math.max(0, seconds) / 60);
  return minutes >= 60 ? `${Math.floor(minutes / 60)}h ${minutes % 60}m` : `${minutes}m`;
}

function renderTraining(training) {
  const t = training || {};
  const busy = busyStages.includes(t.stage);
  $('training-banner').classList.toggle('hidden', !t.run_id);
  document.body.classList.toggle('has-training-banner', !!t.run_id);
  const titles = { exporting: 'Reading all your conversations', preparing: 'Preparing examples for your model', training: 'Learning your voice', validating: 'Checking the model', ready: 'Your new model is ready', failed: 'Training needs attention', interrupted: 'Training interrupted' };
  $('training-stage-title').textContent = titles[t.stage] || 'Your personal model';
  const percent = Math.max(0, Math.min(100, t.progress_percent || 0));
  $('training-percent').textContent = `${percent.toLocaleString(undefined, { maximumFractionDigits: 2 })}%`;
  $('training-main-progress').value = percent;
  $('training-progress').value = percent;
  const metrics = [];
  if (t.messages_scanned != null) metrics.push(`${t.messages_scanned.toLocaleString()} of your outgoing messages scanned`);
  if (t.training_policy === 'owner_only_v1') metrics.push('0 incoming messages in training');
  if (t.stage === 'exporting') metrics.push(`${(t.chats_processed || 0).toLocaleString()} / ${(t.chats_total || 0).toLocaleString()} conversations`);
  if (t.steps_total) metrics.push(`${(t.steps_completed || 0).toLocaleString()} / ${t.steps_total.toLocaleString()} training steps`);
  if (t.prepared_valid) metrics.push(`${t.prepared_valid.toLocaleString()} validation examples`);
  if (busy && t.steps_completed) metrics.push(`~${duration(t.eta_seconds)} remaining`);
  if (t.elapsed_seconds) metrics.push(`${duration(t.elapsed_seconds)} elapsed`);
  $('training-metrics').replaceChildren(...metrics.map(text => element('span', '', text)));
  $('training-scope').textContent = t.error || `Owner-only training. Only your outgoing text is included; incoming messages are excluded from both prompts and targets. Each example uses a fixed neutral prompt. ${t.excluded_messages?.toLocaleString() || 0} reactions, system entries and non-text messages excluded.`;
  return busy;
}

async function api(path, body) {
  const options = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  if (response.status === 401) throw new Error('Your session has expired. Reopen the inbox with start.command.');
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Something went wrong. Try again.');
  return result;
}

function toast(text, error = false) {
  $('toast').textContent = text;
  $('toast').classList.toggle('error', error);
  $('toast').classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('toast').classList.add('hidden'), error ? 7000 : 3200);
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function initials(chat) {
  if (chat.style !== 45 || chat.participants.length > 1) return '♧';
  const title = chat.title;
  if (/^[+\d(]/.test(title)) return title.replace(/\D/g, '').slice(-2);
  return title.split(/[\s@]+/).slice(0, 2).map(x => x[0] || '').join('').toUpperCase();
}

function relativeDate(timestamp) {
  if (!timestamp) return '';
  const date = new Date(timestamp * 1000), now = new Date();
  if (date.toDateString() === now.toDateString()) return timeFormat.format(date);
  if (now - date < 7 * 86400000) return date.toLocaleDateString(undefined, { weekday: 'short' });
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function renderChats() {
  const search = state.query.toLowerCase();
  const chats = state.chats.filter(chat => (state.filter !== 'auto' || chat.auto_reply) && (state.filter !== 'unread' || chat.unread) && [chat.title, ...chat.participants].join(' ').toLowerCase().includes(search));
  $('chat-count').textContent = state.chats.length.toLocaleString();
  const fragment = document.createDocumentFragment();
  for (const chat of chats) {
    const button = element('button', 'chat-row' + (chat.id === state.selected ? ' selected' : ''));
    button.dataset.chat = chat.id;
    button.setAttribute('aria-label', chat.title + (chat.auto_reply ? ', auto replies enabled' : ''));
    button.setAttribute('aria-current', chat.id === state.selected ? 'true' : 'false');
    button.append(element('span', `avatar color-${chat.id % 5}`, initials(chat)));
    const copy = element('div', 'chat-copy'), top = element('div', 'chat-topline'), bottom = element('div', 'chat-bottomline');
    top.append(element('span', 'chat-name', chat.title), element('time', 'chat-time', relativeDate(chat.timestamp)));
    bottom.append(element('div', 'chat-preview', (chat.is_from_me ? 'You: ' : '') + chat.preview));
    if (chat.auto_reply) bottom.append(element('span', 'chat-auto', '✦'));
    else if (chat.unread) bottom.append(element('span', 'unread-dot'));
    copy.append(top, bottom); button.append(copy); fragment.append(button);
  }
  if (!chats.length) fragment.append(element('div', 'loading-state', state.query ? 'No conversations match your search.' : 'No conversations in this view yet.'));
  const position = $('chat-list').scrollTop;
  $('chat-list').replaceChildren(fragment);
  $('chat-list').scrollTop = position;
}

function selectedChat() { return state.chats.find(chat => chat.id === state.selected); }

async function selectChat(id) {
  if (state.dirty && !window.confirm('Leave this chat without saving your instructions?')) return;
  state.dirty = false;
  state.selected = id;
  state.messages = [];
  const request = ++state.request;
  renderChats(); renderSettings();
  $('thread-empty').classList.add('hidden');
  $('thread-content').classList.remove('hidden');
  document.querySelector('.workspace').classList.add('thread-open');
  const chat = selectedChat();
  $('thread-title').textContent = chat.title;
  $('thread-avatar').textContent = initials(chat);
  $('thread-avatar').className = `avatar color-${chat.id % 5}`;
  $('thread-meta').textContent = chat.participants.length > 1 ? `${chat.participants.length} participants · Group conversation` : chat.participants[0] || 'Conversation';
  $('thread-service').textContent = chat.service || 'Messages';
  $('messages').replaceChildren(element('div', 'loading-state', 'Loading your conversation…'));
  $('load-older').classList.add('hidden');
  try {
    const data = await api(`/api/chats/${id}/messages`);
    if (request !== state.request) return;
    state.messages = data.messages; state.hasMore = data.has_more;
    renderMessages();
    $('message-scroll').scrollTop = $('message-scroll').scrollHeight;
  } catch (error) {
    if (request === state.request) $('messages').replaceChildren(element('div', 'loading-state', error.message));
  }
}

function renderSettings() {
  const chat = selectedChat();
  $('chat-settings-empty').classList.toggle('hidden', !!chat);
  $('chat-settings').classList.toggle('hidden', !chat);
  if (!chat) return;
  $('chat-toggle').setAttribute('aria-checked', String(chat.auto_reply));
  $('chat-toggle').disabled = !chat.reply_supported;
  $('chat-toggle-description').textContent = chat.reply_supported ? 'Let your model handle replies in this conversation.' : 'You can browse this chat. Auto replies currently support direct iMessage and SMS chats.';
  $('chat-enabled-label').textContent = chat.auto_reply ? 'ENABLED FOR THIS CHAT' : chat.reply_supported ? 'OFF FOR THIS CHAT' : 'BROWSE ONLY';
  $('chat-enabled-label').classList.toggle('enabled', chat.auto_reply);
  if (!state.dirty) {
    $('instructions').value = chat.instructions;
    $('char-count').textContent = `${chat.instructions.length.toLocaleString()} / 4,000`;
    $('save-status').textContent = 'Only applies to this chat';
  }
  $('thread-footer-text').textContent = chat.auto_reply ? (state.status?.enabled ? `Auto replies enabled · ${state.status.mode === 'preview' ? 'Previewing without sending' : 'Replies will send automatically'}` : 'Auto replies selected for this chat. Turn on the master switch when you’re ready.') : 'Your history stays on this Mac. Set up auto replies on the right.';
}

function renderMessages() {
  const fragment = document.createDocumentFragment();
  let previousDay = '';
  const chat = selectedChat();
  for (const message of state.messages) {
    const date = message.timestamp ? new Date(message.timestamp * 1000) : null;
    const day = date ? date.toDateString() : '';
    if (day && day !== previousDay) { fragment.append(element('div', 'date-separator', dayFormat.format(date))); previousDay = day; }
    if (message.associated_message_type || message.item_type) {
      fragment.append(element('div', 'message-event', message.text || (message.associated_message_type ? 'Reaction to a message' : 'Conversation update')));
      continue;
    }
    const row = element('div', 'message-row' + (message.is_from_me ? ' own' : ''));
    row.dataset.message = message.id;
    if (!message.is_from_me && chat?.participants.length > 1) {
      const participant = chat.participants.indexOf(message.sender);
      row.append(element('div', 'message-sender', chat.participant_names?.[participant] || message.sender || 'Participant'));
    }
    if (message.subject) row.append(element('strong', 'message-sender', message.subject));
    if (message.text) row.append(element('div', 'message-bubble', message.text));
    for (const attachment of message.attachments) {
      const link = element('a', 'message-attachment', '↗ ' + (attachment.name || 'Attachment'));
      link.href = `/api/attachments/${attachment.id}`; link.target = '_blank'; link.rel = 'noopener noreferrer';
      if (['image/jpeg', 'image/png', 'image/gif', 'image/webp'].includes(attachment.mime_type)) {
        const img = element('img', 'message-image');
        img.src = link.href; img.alt = attachment.name || 'Image attachment'; img.loading = 'lazy';
        img.addEventListener('load', () => { if ($('message-scroll').scrollHeight - $('message-scroll').scrollTop - $('message-scroll').clientHeight < 350) $('message-scroll').scrollTop = $('message-scroll').scrollHeight; });
        link.replaceChildren(img);
      }
      row.append(link);
    }
    if (!message.text && !message.attachments.length) row.append(element('div', 'message-event', 'Message content unavailable on this Mac'));
    const caption = element('div', 'message-caption', date ? timeFormat.format(date) : '');
    if (message.is_from_me && (message.is_read || message.is_delivered)) caption.append(element('span', '', message.is_read ? 'Read' : 'Delivered'));
    row.append(caption); fragment.append(row);
  }
  if (!state.messages.length) fragment.append(element('div', 'loading-state', 'No messages are available in this conversation.'));
  $('messages').replaceChildren(fragment);
  $('load-older').classList.toggle('hidden', !state.hasMore);
}

async function olderMessages() {
  if (state.loading || !state.messages.length) return;
  state.loading = true; $('load-older').textContent = 'Loading…';
  const id = state.selected, request = state.request;
  try {
    const page = await api(`/api/chats/${id}/messages?before=${state.messages[0].id}`);
    if (request !== state.request) return;
    const scroll = $('message-scroll'), oldHeight = scroll.scrollHeight, top = scroll.scrollTop;
    state.messages = [...page.messages, ...state.messages]; state.hasMore = page.has_more;
    renderMessages(); scroll.scrollTop = top + scroll.scrollHeight - oldHeight;
  } catch (error) { toast(error.message, true); }
  finally { state.loading = false; $('load-older').textContent = '↑ Load earlier messages'; }
}

function renderStatus() {
  const data = state.status;
  if (!data) return;
  $('master-toggle').setAttribute('aria-checked', String(data.enabled));
  $('master-label').textContent = data.enabled ? 'Auto replies running' : 'Auto replies paused';
  $('master-detail').textContent = data.enabled ? (data.mode === 'preview' ? 'Preview mode · nothing gets sent' : 'Sending to enabled chats') : "You're in control.";
  $('number-label').textContent = data.number;
  $('connection-label').textContent = data.connected ? 'Messages connected' : 'Connection needs attention';
  $('connection-dot').classList.toggle('error', !data.connected);
  $('mode-preview').classList.toggle('active', data.mode === 'preview');
  $('mode-live').classList.toggle('active', data.mode === 'live');
  $('mode-description').textContent = data.mode === 'preview' ? 'Try it out. Replies are saved to Activity without sending a message.' : 'Replies send through Messages to chats you enable. Change modes to pause.';
  const stage = data.training?.stage;
  const busy = renderTraining(data.training);
  $('model-state').textContent = busy ? 'Training' : data.trained ? 'Ready' : 'Not trained';
  $('model-description').textContent = busy ? `${(data.training.progress_percent || 0).toFixed(2)}% of this stage · ${data.training.steps_total?.toLocaleString() || 'Preparing'} examples` : data.trained ? 'Trained on your messages. Ready to sound like you.' : 'Train on your outgoing messages to get started.';
  $('setup-connection').textContent = data.connected ? 'Your messages are connected and stay on this Mac.' : data.error;
  $('setup-connection-badge').textContent = data.connected ? 'Connected ✓' : 'Needs access';
  $('messages-access').classList.toggle('hidden', data.connected);
  $('verify-number').textContent = data.number;
  if (data.sender_verified) $('verify-check').checked = true;
  $('verify-save').textContent = data.sender_verified ? 'Number confirmed ✓' : 'Confirm number';
  $('verify-save').disabled = data.sender_verified;
  $('training-progress').classList.toggle('hidden', !busy);
  $('train-start').disabled = busy || !data.connected;
  $('train-start').textContent = busy ? 'Training in progress…' : data.trained ? 'Train a new version ↗' : 'Train my model ↗';
  $('training-description').textContent = busy ? `${data.training.steps_completed?.toLocaleString() || 0} / ${data.training.steps_total?.toLocaleString() || '…'} steps · ${data.training.progress_percent || 0}% of this stage. Only your outgoing text is used. Incoming messages are never training context.` : data.training?.error || (data.trained ? 'Your owner-only model is ready. A new run trains on your outgoing messages only, across all conversations.' : 'Train locally on your outgoing messages only. Earlier mixed-context models are disabled. Your history stays on this Mac.');
  const contacts = data.contacts || {};
  $('contacts-description').textContent = contacts.stage === 'connected' ? `${contacts.count.toLocaleString()} contacts synced${contacts.limited ? ' (limited access)' : ''}. Names appear automatically in your inbox. Contact data stays on this Mac.` : contacts.error || 'Connect macOS Contacts to show names beside your messages. Only names, phone numbers and email addresses are read.';
  $('contacts-sync').textContent = contacts.stage === 'requesting' ? 'Waiting for macOS permission…' : contacts.stage === 'connected' ? 'Refresh contacts' : contacts.stage === 'permission_needed' ? 'Retry contacts access' : 'Connect contacts';
  $('contacts-sync').disabled = contacts.stage === 'requesting';
  if (state.selected) renderSettings();
}

async function refresh(initial = false) {
  try {
    const [statusResult, inboxResult] = await Promise.allSettled([api('/api/status'), api('/api/chats')]);
    if (statusResult.status !== 'fulfilled') throw statusResult.reason;
    const status = statusResult.value;
    state.status = status;
    if (!status.connected) {
      renderStatus();
      $('chat-list').replaceChildren(element('div', 'loading-state', status.error));
      if (initial) $('setup-dialog').showModal();
      return;
    }
    if (inboxResult.status !== 'fulfilled') throw inboxResult.reason;
    state.chats = inboxResult.value.chats;
    renderStatus(); renderChats();
    if (state.selected && !state.loading) {
      const request = state.request, id = state.selected;
      const latest = state.messages.at(-1)?.id;
      if (latest) {
        const page = await api(`/api/chats/${id}/messages?after=${latest}`);
        if (request === state.request && page.messages.length) {
          const scroll = $('message-scroll'), bottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 180;
          state.messages.push(...page.messages); renderMessages();
          if (bottom) scroll.scrollTop = scroll.scrollHeight;
        }
      }
    }
    if (initial && !status.connected) $('setup-dialog').showModal();
  } catch (error) {
    if (initial) $('chat-list').replaceChildren(element('div', 'loading-state', error.message));
    $('connection-label').textContent = 'Unable to connect'; $('connection-dot').classList.add('error');
    if (!initial) toast(error.message, true);
  }
}

async function mutateControl(payload) {
  try { state.status = await api('/api/control', payload); renderStatus(); return true; }
  catch (error) { toast(error.message, true); return false; }
}

async function updateChat(payload) {
  const id = state.selected;
  try {
    const chat = await api(`/api/chats/${id}`, payload);
    state.chats = state.chats.map(x => x.id === id ? chat : x);
    if (id === state.selected) renderSettings();
    renderChats(); return true;
  } catch (error) { toast(error.message, true); return false; }
}

async function activity() {
  $('activity-dialog').showModal();
  $('activity-list').replaceChildren(element('div', 'loading-state', 'Loading reply activity…'));
  try {
    const data = await api('/api/activity');
    const fragment = document.createDocumentFragment();
    for (const item of data.activity) {
      const card = element('article', 'activity-item'), header = element('header');
      const chat = state.chats.find(x => x.id === item.chat_id);
      header.append(element('span', '', chat?.title || 'Conversation'), element('span', 'activity-status', item.status));
      card.append(header, element('p', '', item.reply), element('small', 'small-label', new Date(item.created * 1000).toLocaleString()));
      if (item.error) card.append(element('p', '', `Delivery uncertain (${item.error}). This reply will not be retried.`));
      fragment.append(card);
    }
    if (!data.activity.length) fragment.append(element('div', 'loading-state', 'A quiet start. Once replies are enabled, previews and send attempts will appear here.'));
    $('activity-list').replaceChildren(fragment);
  } catch (error) { $('activity-list').replaceChildren(element('div', 'loading-state', error.message)); }
}

$('chat-list').addEventListener('click', event => { const row = event.target.closest('[data-chat]'); if (row) selectChat(Number(row.dataset.chat)); });
$('search').addEventListener('input', event => { state.query = event.target.value; renderChats(); });
document.querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click', () => { state.filter = button.dataset.filter; document.querySelectorAll('[data-filter]').forEach(x => { x.classList.toggle('active', x === button); x.setAttribute('aria-selected', String(x === button)); }); renderChats(); }));
$('refresh').addEventListener('click', () => refresh());
$('load-older').addEventListener('click', olderMessages);
$('master-toggle').addEventListener('click', async () => { if (!state.status) return; $('master-toggle').disabled = true; const enabled = !state.status.enabled; if (await mutateControl({ enabled })) toast(enabled ? 'Replies enabled for your selected chats.' : 'All automatic replies paused.'); $('master-toggle').disabled = false; });
$('chat-toggle').addEventListener('click', async () => { const chat = selectedChat(); if (!chat) return; $('chat-toggle').disabled = true; if (await updateChat({ enabled: !chat.auto_reply })) toast(chat.auto_reply ? 'Auto replies turned off for this chat.' : 'Chat enabled. The master switch controls when replies run.'); $('chat-toggle').disabled = !selectedChat()?.reply_supported; });
$('instructions').addEventListener('input', () => { state.dirty = true; $('save-status').textContent = 'Unsaved changes'; $('char-count').textContent = `${$('instructions').value.length.toLocaleString()} / 4,000`; });
$('save-instructions').addEventListener('click', async () => { if (!state.selected) return; const id = state.selected; $('save-instructions').disabled = true; if (await updateChat({ instructions: $('instructions').value })) { if (state.selected === id) { state.dirty = false; $('save-status').textContent = 'Saved for this chat ✓'; } toast('Instructions saved.'); } $('save-instructions').disabled = false; });
$('mode-preview').addEventListener('click', async () => { if (state.status?.mode !== 'preview' && await mutateControl({ mode: 'preview' })) toast('Preview mode selected. Replies paused.'); });
$('mode-live').addEventListener('click', async () => { if (state.status?.mode !== 'live' && await mutateControl({ mode: 'live' })) toast('Automatic sending selected. Turn on the master switch to start.'); });
$('mode-info').addEventListener('click', () => toast('Preview saves replies to Activity. Automatic sending delivers them through Messages. Changing modes pauses replies.'));
for (const id of ['setup-open', 'model-setup']) $(id).addEventListener('click', () => { renderStatus(); $('setup-dialog').showModal(); });
$('activity-open').addEventListener('click', activity);
document.querySelectorAll('.dialog-close').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
document.querySelectorAll('dialog').forEach(dialog => dialog.addEventListener('click', event => { if (event.target === dialog) { const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close(); } }));
$('verify-save').addEventListener('click', async () => { if (!$('verify-check').checked) return toast('Check your number in Messages, then tick the confirmation.', true); if (await mutateControl({ verified_number: state.status.number })) toast('Sending number confirmation saved.'); });
$('train-start').addEventListener('click', async () => { $('train-start').disabled = true; try { await api('/api/train', {}); toast('Local training started. You can keep browsing.'); await refresh(); } catch (error) { toast(error.message, true); $('train-start').disabled = false; } });
$('contacts-open').addEventListener('click', () => { renderStatus(); $('setup-dialog').showModal(); $('contacts-sync').focus(); });
$('contacts-sync').addEventListener('click', async () => { $('contacts-sync').disabled = true; try { await api('/api/contacts/sync', {}); toast('Contacts sync started. Allow access if macOS asks.'); setTimeout(() => refresh(), 1500); } catch (error) { toast(error.message, true); $('contacts-sync').disabled = false; } });
$('messages-access').addEventListener('click', async () => { try { await api('/api/permissions/messages', {}); toast('Enable Inbox in Full Disk Access, then quit and reopen the app.'); } catch (error) { toast(error.message, true); } });
$('details-toggle').addEventListener('click', () => document.querySelector('.workspace').classList.toggle('show-settings'));
$('settings-close').addEventListener('click', () => document.querySelector('.workspace').classList.remove('show-settings'));
$('mobile-back').addEventListener('click', () => document.querySelector('.workspace').classList.remove('thread-open', 'show-settings'));
document.addEventListener('keydown', event => { if ((event.metaKey || event.ctrlKey) && event.key === 'k') { event.preventDefault(); $('search').focus(); } });
window.addEventListener('beforeunload', event => { if (state.dirty) { event.preventDefault(); event.returnValue = ''; } });
refresh(true);
setInterval(() => { if (!document.hidden) refresh(); }, 6000);
