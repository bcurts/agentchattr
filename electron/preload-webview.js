'use strict';

const { ipcRenderer } = require('electron');

function sendNotification(payload) {
  ipcRenderer.sendToHost('send-notification', payload);
}

function checkForNotification(data) {
  if (!data || typeof data !== 'object') {
    return;
  }

  if (data.type === 'message') {
    const body = typeof data.body === 'string' ? data.body : '';

    if (body.includes('@user') || body.includes('@all')) {
      sendNotification({
        title: `${data.sender} mentioned you`,
        body: body.substring(0, 100),
        channel: data.channel,
      });
    }

    return;
  }

  if (data.type === 'agent_log') {
    const line = typeof data.line === 'string' ? data.line : '';

    if (/error|crash|exception/i.test(line)) {
      sendNotification({
        title: `Agent ${data.name} error`,
        body: line.substring(0, 100),
      });
    }

    return;
  }

  if (data.type === 'job_update' && ['completed', 'failed'].includes(data.status)) {
    sendNotification({
      title: `Job ${data.status}`,
      body: data.job_name || 'Unknown job',
    });
  }
}

function addSocketMessageListener(socket) {
  socket.addEventListener('message', (event) => {
    if (typeof event.data !== 'string') {
      return;
    }

    try {
      const data = JSON.parse(event.data);
      checkForNotification(data);
    } catch (error) {
      // Ignore non-JSON frames to preserve native behaviour.
    }
  });
}

function installWebSocketInterceptor() {
  const OrigWebSocket = window.WebSocket;

  if (typeof OrigWebSocket !== 'function') {
    return;
  }

  function WrappedWebSocket(...args) {
    const socket = new OrigWebSocket(...args);
    addSocketMessageListener(socket);
    return socket;
  }

  WrappedWebSocket.CONNECTING = OrigWebSocket.CONNECTING;
  WrappedWebSocket.OPEN = OrigWebSocket.OPEN;
  WrappedWebSocket.CLOSING = OrigWebSocket.CLOSING;
  WrappedWebSocket.CLOSED = OrigWebSocket.CLOSED;
  WrappedWebSocket.prototype = OrigWebSocket.prototype;

  Object.setPrototypeOf(WrappedWebSocket, OrigWebSocket);

  window.WebSocket = WrappedWebSocket;
}

if (document.readyState === 'loading') {
  window.addEventListener('DOMContentLoaded', installWebSocketInterceptor, { once: true });
} else {
  installWebSocketInterceptor();
}
