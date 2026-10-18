"use strict";
const token = document.querySelector('meta[name="app-token"]').content;
let state;
let currentSession;
const $ = (id) => document.getElementById(id);
const subjectNames = ["理论法", "刑法", "刑事诉讼法", "民事综合", "行政法", "商法"];
$("source").elements.preset.addEventListener("change", event=>{
  const selected={lijia:["行政法李佳","行政法","https://weibo.com/u/1552849431"],mengxiangui:["民商法孟献贵","民事综合","https://weibo.com/2342740757?refer_flag=1001030103_"],zuoning:["左宁刑诉","刑事诉讼法","https://weibo.com/u/3153511812"]}[event.target.value];
  if(selected){$("source").elements.name.value=selected[0];$("source").elements.subject.value=selected[1];$("source").elements.url.value=selected[2];}
});
for (const select of document.querySelectorAll(".subject-select")) {
  const names = select.dataset.noGeneral ? subjectNames : ["综合", ...subjectNames];
  for (const name of names) select.add(new Option(name, name));
}
function notice(message, error=false) {
  $("notice").textContent = message;
  $("notice").className = error ? "error" : "";
}
async function request(path, body) {
  if(body && !state) throw new Error("个人实例尚在读取，请稍后再保存。");
  const response = await fetch(path, {headers: {"X-App-Token":token, "Content-Type":"application/json"},
    method: body ? "POST" : "GET", body: body ? JSON.stringify({...body, version:state.version}) : undefined});
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 409) await load();
    throw new Error(result.error || "操作未完成");
  }
  return result;
}
function renderItems(id, rows, name, detail) {
  const list = $(id); list.replaceChildren();
  if (!rows.length) {
    const item = document.createElement("li"); item.textContent="尚未登记，可以稍后补充。"; list.append(item);
  }
  for (const row of rows) {
    const item=document.createElement("li"), small=document.createElement("small");
    item.textContent=name(row); small.textContent=detail(row); item.append(small); list.append(item);
  }
}
function render() {
  const profile=state.profile, config=profile.config, form=$("settings");
  for (const key of ["year", "participation", "elective", "selected_ai", "stage_override"])
    form.elements[key].value=config[key] ?? "";
  form.elements.search_mode.value=config.authorization.search_mode;
  form.elements.scope.value=config.authorization.scope;
  form.elements.online_ai.checked=config.authorization.online_ai;
  form.elements.theory_target_chars.value=config.theory_target_chars||1000;
  form.elements.timezone.value=config.timezone;
  const auto=$("automation");
  for (const [key,value] of Object.entries(config.automation)) {
    if (typeof value === "boolean") auto.elements[key].checked=value;
    else auto.elements[key].value=value;
  }
  $("next-action").textContent=state.next_action;
  $("steps").replaceChildren();
  for (const step of state.setup_steps) {
    const li=document.createElement("li");li.textContent=`${step.ready ? "✓" : "○"} ${step.name}${step.detail ? " · "+step.detail : ""}`;$("steps").append(li);
  }
  const statuses={text_extracted:"文字已提取，题面与答案待核验",text_extracted_layout_unverified:"文字已提取，版式与图片待核验",ocr_pending:"已保存，等待识别图片文字",pdf_extractor_pending:"已保存，PDF文字提取尚需依赖",extraction_failed:"已保存原件，文字提取失败",original_saved:"已保存原件，由所选AI按需读取此格式",processing_pending:"原件已完整保存，由所选AI分段解析"};
  renderItems("materials",profile.materials,r=>r.name,r=>`${r.subject} · ${statuses[r.status] || r.status} · ${Math.ceil(r.size/1024)} KB`);
  renderItems("courses",profile.course_units,r=>r.title,r=>`${r.subject} · 实际完成日期 ${r.completed_on}`);
  const sourceStatus={disabled:"未启用",awaiting_browser:"等待所选AI浏览器",ready:"已具备读取条件",captured:"已保存实际读取结果",checked_no_items:"本次已读取，无条目",failed:"读取失败，需检查来源"};
  renderItems("sources",profile.sources,r=>r.name,r=>`${r.subject} · ${sourceStatus[r.execution_status] || "待核验"} · ${r.url}`);
  const chosenSource=$("source-picker").value;
  $("source-picker").replaceChildren();
  for(const s of profile.sources) $("source-picker").add(new Option(s.name,s.id));
  if(profile.sources.some(s=>s.id===chosenSource))$("source-picker").value=chosenSource;
  updateSourceEditor();
  const labels={objective_end:"客观题考试结束",results:"客观题成绩公布",subjective:"主观题考试"};
  const dateStatus={pending:"待检索",failed:"本次检索失败",provided:"已提供，待核验",verified:"已有官方依据",not_found:"尚未查到适用公告",conflict:"依据冲突，待处理",unpublished:"已确认尚未公布"};
  renderItems("exam-dates",Object.entries(config.exam_dates),r=>`${labels[r[0]]}：${r[1].date || "待检索"}`,r=>`${dateStatus[r[1].status]} · ${r[1].evidence || "尚无可核验的官方依据"}`);
  $("plan").replaceChildren();
  const p=state.plan;
  const metrics=[[p.stage.number ? `第${p.stage.number}阶段` : "待确认","当前阶段"],[p.case_target,"案例计划量"],[p.case_completed,"今日已完成"],[p.historical_optional?"自选":p.historical_target,"历史订正计划量"]];
  for (const [value,label] of metrics) {
    const box=document.createElement("div"),b=document.createElement("b"),span=document.createElement("span");
    box.className="metric";b.textContent=value;span.textContent=label;box.append(b,span);$("plan").append(box);
  }
  $("plan-note").textContent=`${p.stage.reason}。生效科目：${p.subjects.join("、")}。理论安排：${p.theory_mode}。${p.resume_first.length ? "有未完成原题，先续接。" : ""}推荐和导入均不代表学习已完成。`;
}
async function load() { state=await request("/api/state");render(); }
async function operation(operation,payload){
  const result=await request("/api/operate",{operation,payload});state=result.state;render();return result.result;
}
function actionButton(id,action){$(id).addEventListener("click",async()=>{const button=$(id);button.disabled=true;try{await action();}catch(error){notice(error.message,true);}finally{button.disabled=false;}});}
function displaySession(session){
  currentSession=session;const view=$("question-view");view.replaceChildren();view.hidden=false;
  for(const content of [session.question.facts,...session.question.questions]){const p=document.createElement("p");p.textContent=content;p.style.whiteSpace="pre-wrap";view.append(p);}
  if(session.knowledge){const p=document.createElement("p");p.textContent="知识点学习\n"+session.knowledge;p.style.whiteSpace="pre-wrap";view.append(p);}
  $("response").hidden=!["awaiting_answer","awaiting_correction"].includes(session.status);
  $("study-status").textContent=({awaiting_answer:"请按完整题面作答。",awaiting_correction:"先学习上面的知识点，再提交你的订正答复。",awaiting_grade:"真实答复已保存，请回到AI继续评价。",awaiting_correction_grade:"订正已保存，请由AI按知识点、案情、错漏、答案讲评。"})[session.status]||session.status;
}
actionButton("refresh-dates",async()=>{notice("正在检索适用年度司法部公告…");const result=await operation("dates",{});notice(Object.values(result.outcomes).some(v=>v==="verified")?"已保存核验日期，详细依据见列表。":"本次未获取完整日期，请让所选AI实际检索官方公告后继续；已有核验结果保留。");});
actionButton("client-config",async()=>{const result=await request("/api/integration",{});$("integration-result").textContent=`连接文件已生成：${result.result.directory}。在所选AI添加对应MCP配置，再发送其中的“首次使用”提示词。文件生成不代表已安装连接。`;});
actionButton("start-case",async()=>{const result=await operation("learning",{action:"issue",payload:{on:state.plan.day}});if(result.session)displaySession(result.session);else notice(result.reason,true);});
actionButton("start-history",async()=>{const result=await operation("learning",{action:"issue",payload:{on:state.plan.day,mode:"history",authorization:"用户点击“我选择历史订正”，明确要求开始本次历史订正"}});if(result.session)displaySession(result.session);else notice(result.reason,true);});
actionButton("start-theory",async()=>{const result=await operation("learning",{action:"theory_start",payload:{on:state.plan.day}});$("study-status").textContent="本轮完整主题："+result.activity.titles.join("、")+"。背完后向所选AI反馈，再按当前阶段继续；未反馈不记完成。";const view=$("question-view");view.hidden=false;view.replaceChildren();for(const theme of result.themes||[]){const p=document.createElement("p");p.textContent=theme.title+"\n"+theme.text;p.style.whiteSpace="pre-wrap";view.append(p);}$("response").hidden=true;});
bind("response",async(form)=>{if(!currentSession)throw new Error("尚无待答题目");const result=await operation("learning",{action:"submit_answer",payload:{session_id:currentSession.id,on:state.plan.day,answer:form.elements.answer.value,complete:form.elements.complete.checked}});currentSession.status=result.status;displaySession(currentSession);form.elements.answer.value="";notice("真实答复已保存，请回到所选AI继续。");});
bind("source-settings",async(form)=>{await operation("source",{action:"configure",payload:{source_id:form.elements.source_id.value,mode:form.elements.mode.value,enabled:form.elements.enabled.checked,identity_verification:form.elements.identity_verification.value,authorization:form.elements.authorization.value}});notice("来源设置已保存；实际采集状态见来源列表。");});
actionButton("run-monitor",async()=>{const result=await request("/api/monitor",{});state=result.state;render();const rows=result.result.sources||[];$("background-result").textContent=result.result.status==="disabled"?"监测尚未启用，请先保存后台选择和单个来源授权。":result.result.status==="waiting_stage2"?result.result.reason:`本次检查 ${rows.length} 个来源；${rows.filter(r=>r.status==="requires_client_browser").length} 个等待AI浏览器，${rows.filter(r=>r.status==="failed").length} 个读取失败。结果和待核验内容保存在同一个人实例。`;});
actionButton("prepare-schedule",async()=>{const result=await operation("schedule_enable",{instruction:"用户点击指定本机并生成定时任务文件，确认此机为唯一自动化设备"});$("background-result").textContent=`文件已生成：${result.directory}。尚未安装系统任务，请按docs/自动化与监测.md安装并核验实际运行。`;});
for(const [id,value,evidence] of [["pause-study","paused","用户点击暂停学习"],["resume-study","active","用户点击恢复学习"]])actionButton(id,async()=>{const result=await request("/api/record",{record:{kind:"lifecycle",value,evidence}});state=result.state;render();notice(value==="paused"?"学习已暂停，真实进度保留。":"学习已恢复，按当前阶段继续。");});
function bind(id, action) {
  $(id).addEventListener("submit",async(event)=>{
    event.preventDefault();const button=event.submitter;button.disabled=true;
    try { await action(event.currentTarget); }
    catch(error) { notice(error.message,true); }
    finally { button.disabled=false; }
  });
}
async function saveConfig(config) {
  const result=await request("/api/config",{config});state=result.state;render();notice("设置已保存，可以继续下一步。");
}
bind("settings",async(form)=>saveConfig({year:Number(form.elements.year.value),elective:form.elements.elective.value,
  timezone:form.elements.timezone.value,
  participation:form.elements.participation.value,selected_ai:form.elements.selected_ai.value,
  theory_target_chars:Number(form.elements.theory_target_chars.value),
  stage_override:form.elements.stage_override.value ? Number(form.elements.stage_override.value) : null,
  authorization:{search_mode:form.elements.search_mode.value,scope:form.elements.scope.value,online_ai:form.elements.online_ai.checked}}));
bind("material",async(form)=>{
  const file=form.elements.file.files[0];if(!file)throw new Error("请先选择文件");
  if(!state)throw new Error("个人实例尚在读取，请稍后再上传。");
  notice("正在上传并保存原件，请等待完成…");
  const response=await fetch("/api/material-upload",{method:"POST",headers:{"X-App-Token":token,"Content-Type":"application/octet-stream","X-File-Name":encodeURIComponent(file.name),"X-File-Subject":encodeURIComponent(form.elements.subject.value),"X-File-Category":form.elements.category.value,"X-Instance-Version":state.version},body:file});
  const result=await response.json();
  if(!response.ok){if(response.status===409)await load();throw new Error(result.error||"资料上传未完成");}
  state=result.state;render();form.reset();notice(result.result.duplicate?"这份资料已经导入，已跳过重复入库。":"资料已保存。提取状态见资料列表，尚未标记可出题或已学习。");
});
bind("automation",async(form)=>saveConfig({automation:{learning_requested:form.elements.learning_requested.checked,
  learning_time:form.elements.learning_time.value,monitor_requested:form.elements.monitor_requested.checked,
  monitor_time:form.elements.monitor_time.value,daily_limit:Number(form.elements.daily_limit.value)}}));
bind("source",async(form)=>{
  const result=await request("/api/source",{source:{name:form.elements.name.value,url:form.elements.url.value,subject:form.elements.subject.value,requested:false}});
  state=result.state;render();form.reset();notice("候选来源已登记，尚未启动采集。");
});
bind("course",async(form)=>{
  const result=await request("/api/record",{record:{kind:"course",title:form.elements.title.value,
    subject:form.elements.subject.value,completed_on:state.plan.day,evidence:form.elements.evidence.value}});
  state=result.state;render();form.reset();notice("已按你的反馈登记课程完成，不会自动标记题目已答或已掌握。");
});
function updateSourceEditor(){const source=state?.profile.sources.find(s=>s.id===$("source-picker").value);if(!source)return;const form=$("source-settings");form.elements.mode.value=source.mode||"browser";form.elements.enabled.checked=source.requested;form.elements.identity_verification.value=source.identity_verification||"";form.elements.authorization.value=source.authorization||"";}
$("source-picker").addEventListener("change",updateSourceEditor);
load().catch(error=>notice(error.message,true));
