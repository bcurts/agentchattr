const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const src = fs.readFileSync('static/chat.js', 'utf8');
const part = src.slice(src.indexOf('function getMentionCandidates()'), src.indexOf('function updateSendButton()'));
function setup(text) {
  let keydown, scrolled = [];
  const input = {value:text, selectionStart:text.length, style:{}, scrollHeight:0, addEventListener(type, fn) {if(type === 'keydown') keydown=fn;}, focus(){}, setSelectionRange(a){this.selectionStart=a;}};
  const menu = {children:[], classList:{add(){},remove(){}}, set innerHTML(v){this.children=[];}, appendChild(row){this.children.push(row);}, querySelectorAll(){return this.children;}};
  const document = {getElementById(id){return id === 'input' ? input : menu;},createElement(){return {dataset:{},classList:{toggle(){}},addEventListener(){},scrollIntoView(opts){scrolled.push(opts.block);}};}, querySelector(){return {classList:{toggle(){}}};}};
  const ctx=vm.createContext({document,agentConfig:{c10:{},zeta:{},c2:{},agy:{},c1:{},pending:{state:'pending'}},escapeHtml:s=>s,slashMenuVisible:false,mentionMenuVisible:false,mentionMenuIndex:0,mentionMenuStart:0,restartVoiceAfterEdit(){},updateSendButton(){},updateSlashMenu(){},updateSchedulePopoverState(){},setInterval(){},channelUploads:0,pendingAttachments:[]});
  vm.runInContext(part,ctx); ctx.setupInput(); ctx.updateMentionMenu();
  return {ctx,input,menu,scrolled, key(key){let prevented=false;keydown({key,shiftKey:false,preventDefault(){prevented=true;}});return prevented;}};
}
test('summary autocomplete sorts, scrolls and selects without sending',()=>{
  const s=setup('/summary @');
  assert.deepEqual(s.menu.children.map(r=>r.dataset.name),['agy','all agents','c1','c2','c10','zeta']);
  assert.equal(s.key('ArrowDown'),true);assert.equal(s.key('ArrowDown'),true);assert.deepEqual(s.scrolled,['nearest','nearest']);
  assert.equal(s.key('Enter'),true);assert.equal(s.input.value,'/summary @c1 ');
});
test('escape cancels and ordinary mentions use the same sorted menu',()=>{
  const s=setup('hello @');assert.equal(s.menu.children[0].dataset.name,'agy');
  assert.equal(s.key('ArrowUp'),true);assert.equal(s.key('Escape'),true);assert.equal(s.input.value,'hello @');
});

