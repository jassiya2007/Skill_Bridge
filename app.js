const API_BASE = window.SKILLBRIDGE_API_BASE || 'http://127.0.0.1:8000/api';

const $ = (sel, root=document) => root.querySelector(sel);
const $$ = (sel, root=document) => [...root.querySelectorAll(sel)];

function fmt(n){ return new Intl.NumberFormat('en-IN').format(n); }
function escapeHtml(s){ return String(s ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }

async function api(path, options={}){
  const token=localStorage.getItem('skillbridge_token');
  const headers={...(options.headers||{})};
  if(token) headers.Authorization=`Bearer ${token}`;
  const res = await fetch(API_BASE + path, {...options,headers});
  if(!res.ok) throw new Error(await res.text());
  return res.json();
}

function setText(selector, value){ const el=$(selector); if(el) el.textContent=value; }

async function loadOverview(){
  try{
    const d = await api('/overview');
    const stats = $$('.hero-stats div');
    if(stats[0]) stats[0].querySelector('strong').textContent = fmt(d.students);
    if(stats[0]) stats[0].querySelector('span').textContent = 'Student profiles';
    if(stats[1]) stats[1].querySelector('strong').textContent = fmt(d.skill_taxonomy_size);
    if(stats[1]) stats[1].querySelector('span').textContent = 'Normalized skills';
    if(stats[2]) stats[2].querySelector('strong').textContent = d.placement_rate + '%';
    if(stats[2]) stats[2].querySelector('span').textContent = 'Historical placement';

    const preview = $('.dashboard-preview');
    if(preview){
      const h2 = preview.querySelector('.score-card h2');
      if(h2) h2.textContent = d.placement_rate + '%';
      const welcome = preview.querySelector('.preview-header h3');
      if(welcome) welcome.textContent = `${fmt(d.jobs)} jobs · ${fmt(d.courses)} courses`;
    }
  }catch(e){ console.warn('Backend unavailable:', e.message); }
}

function openAssessment(){
  let modal = $('#assessmentModal');
  if(!modal){
    modal = document.createElement('div');
    modal.id='assessmentModal';
    modal.className='sb-modal';
    modal.innerHTML=`
      <div class="sb-modal-card">
        <button class="sb-close" aria-label="Close">×</button>
        <div class="badge">LIVE CAREER ASSESSMENT</div>
        <h2>Find your skill gap</h2>
        <p class="sb-muted">This form sends your profile to the local SkillBridge API and compares it with the selected role.</p>
        <form id="assessmentForm" class="sb-form">
          <label>Target role<select name="target_role" id="targetRole"></select></label>
          <div class="sb-grid">
            <label>CGPA<input name="cgpa" type="number" step="0.01" min="0" max="4" value="3.2"></label>
            <label>Attendance %<input name="attendance" type="number" min="0" max="100" value="85"></label>
            <label>Programming / 10<input name="programming" type="number" min="0" max="10" value="7"></label>
            <label>Projects<input name="projects" type="number" min="0" max="20" value="4"></label>
            <label>Internships<input name="internships" type="number" min="0" max="10" value="2"></label>
            <label>Communication / 10<input name="communication" type="number" min="0" max="10" value="7"></label>
            <label>Problem Solving / 10<input name="problem_solving" type="number" min="0" max="10" value="7"></label>
            <label>Interview Score<input name="interview_score" type="number" min="0" max="100" value="75"></label>
          </div>
          <button class="btn btn-primary btn-large" type="submit">Analyze My Profile →</button>
        </form>
        <div id="assessmentResult" class="sb-result"></div>
      </div>`;
    document.body.appendChild(modal);
    modal.addEventListener('click', e=>{ if(e.target===modal || e.target.classList.contains('sb-close')) modal.classList.remove('show'); });
    loadRolesInto($('#targetRole'));
    $('#assessmentForm').addEventListener('submit', submitAssessment);
  }
  modal.classList.add('show');
}

async function loadRolesInto(select){
  if(!select) return;
  try{
    const roles=await api('/roles?limit=50');
    select.innerHTML=roles.map(r=>`<option value="${escapeHtml(r.role)}">${escapeHtml(r.role)} (${fmt(r.jobs)} jobs)</option>`).join('');
    const preferred=[...select.options].find(o=>/data analyst/i.test(o.value));
    if(preferred) select.value=preferred.value;
  }catch(e){ select.innerHTML='<option>Data Analyst</option>'; }
}

async function submitAssessment(e){
  e.preventDefault();
  const f=new FormData(e.target);
  const num=k=>Number(f.get(k));
  const payload={
    target_role:f.get('target_role'), cgpa:num('cgpa'), attendance:num('attendance'), programming:num('programming'),
    projects:num('projects'), internships:num('internships'), communication:num('communication'),
    problem_solving:num('problem_solving'), interview_score:num('interview_score'), study_hours:10,
    certifications:1,hackathons:1,leadership:6,resume_score:75,teamwork:7,english:7
  };
  const out=$('#assessmentResult'); out.innerHTML='<div class="sb-loading">Analyzing your profile…</div>';
  try{
    const d=await api('/assessment',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    out.innerHTML=`
      <div class="sb-result-grid">
        <div><span>Placement estimate</span><strong>${d.placement_probability}%</strong></div>
        <div><span>Career readiness</span><strong>${d.readiness_score}%</strong></div>
      </div>
      <h3>Priority skill gaps</h3>
      ${d.skill_gaps.length ? d.skill_gaps.map(g=>`<div class="sb-gap"><span>${escapeHtml(g.skill)}</span><b>${g.gap_percent}% gap</b><small>${g.priority}</small></div>`).join('') : '<p>No major gaps detected from the available assessment signals.</p>'}
      <p class="sb-note">${escapeHtml(d.model_note)}</p>`;
  }catch(err){ out.innerHTML=`<div class="sb-error">Could not reach the backend. Start the FastAPI server on port 8000.</div>`; }
}


async function analyzeRole(){
  const role=$('#liveRole').value; const location=$('#liveLocation').value.trim();
  const experience=$('#liveExperience').value.trim(); const workType=$('#liveWorkType').value.trim(); const minSalary=$('#liveMinSalary').value;
  const filters=new URLSearchParams({role,location,experience,work_type:workType});
  if(minSalary) filters.set('min_salary',minSalary);
  const result=$('#liveResult'); result.innerHTML='<div class="sb-loading">Analyzing job postings and course coverage…</div>';
  try{
    const [d,courses]=await Promise.all([
      api(`/role-analysis?${filters}`),
      api(`/courses?${filters}&limit=5`)
    ]);
    const skills=d.skills||[];
    result.innerHTML=`
      <div class="sb-kpis"><div><span>Matching jobs</span><strong>${fmt(d.job_count)}</strong></div><div><span>Median salary</span><strong>${d.median_salary_inr ? '₹'+fmt(d.median_salary_inr) : 'N/A'}</strong></div><div><span>Top function</span><strong>${escapeHtml(d.top_functions?.[0]?.function||'—')}</strong></div></div>
      <div class="sb-columns">
        <div><h3>Industry skill demand</h3>${skills.length?skills.slice(0,10).map(s=>`<div class="sb-bar-row"><span>${escapeHtml(s.skill)}</span><div><i style="width:${s.demand_percent}%"></i></div><b>${s.demand_percent}%</b></div>`).join(''):'<p>No extracted skills for this role in the current sample.</p>'}</div>
        <div><h3>Recommended courses</h3>${courses.map(c=>`<div class="sb-course"><div><strong>${escapeHtml(c.title)}</strong><small>${escapeHtml(c.organization)} · ${escapeHtml(c.difficulty||'')}</small></div><b>${c.alignment_percent}%</b></div>`).join('')}</div>
      </div>
      <p class="sb-note">${escapeHtml(d.data_note)}</p>`;
  }catch(e){ result.innerHTML='<div class="sb-error">Could not load role intelligence. Make sure the backend is running.</div>'; }
}

function openAccount(){
  let modal=$('#accountModal');
  if(!modal){
    modal=document.createElement('div'); modal.id='accountModal'; modal.className='sb-modal';
    modal.innerHTML=`<div class="sb-modal-card"><button class="sb-close" aria-label="Close">×</button><div class="badge">LOCAL ACCOUNT</div><h2>Save your progress</h2><p class="sb-muted">Your account and assessment history are stored locally in this prototype.</p><form id="accountForm" class="sb-form"><label>Email<input name="email" type="email" required></label><label>Password<input name="password" type="password" minlength="8" required></label><div class="hero-buttons"><button class="btn btn-primary" name="mode" value="register">Create account</button><button class="btn btn-outline" name="mode" value="login">Log in</button></div></form><div id="accountResult" class="sb-result"></div></div>`;
    document.body.appendChild(modal);
    modal.addEventListener('click',e=>{if(e.target===modal||e.target.classList.contains('sb-close'))modal.classList.remove('show');});
    $('#accountForm').addEventListener('submit',async e=>{e.preventDefault();const form=new FormData(e.target);const mode=e.submitter?.value||'login';const out=$('#accountResult');try{const d=await api(`/auth/${mode}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:form.get('email'),password:form.get('password')})});localStorage.setItem('skillbridge_token',d.token);localStorage.setItem('skillbridge_email',d.email);out.innerHTML='<div class="sb-note">Signed in. Future assessments will be saved.</div>';setTimeout(()=>modal.classList.remove('show'),700);}catch(err){
  let message = 'Could not sign in. Please try again.';

  try {
    const errorData = JSON.parse(err.message);

    if (errorData.detail) {
      message = errorData.detail;
    }
  } catch (_) {
    // Keep the default message if the response is not JSON
  }

  out.innerHTML = `<div class="sb-error">${escapeHtml(message)}</div>`;
}});
  }
  modal.classList.add('show');
}

function addCareerTools(){
  if($('#careerTools')) return;
  const section=document.createElement('section'); section.id='careerTools'; section.className='sb-live-section';
  section.innerHTML=`<div class="section-heading"><div class="badge">YOUR CAREER TOOLKIT</div><h2>Build a practical <span>next-step plan.</span></h2><p>Use your résumé, target role, and available time to turn insight into action.</p></div><div class="sb-live-card"><div class="sb-columns"><div><h3>Résumé skill scan</h3><form id="resumeForm" class="sb-form"><label>Target role<select id="resumeRole"></select></label><label>Résumé (PDF, DOCX, or TXT)<input name="file" type="file" accept=".pdf,.docx,.txt" required></label><button class="btn btn-primary" type="submit">Analyze résumé →</button></form><div id="resumeResult" class="sb-result"></div></div><div><h3>Personal learning roadmap</h3><form id="roadmapForm" class="sb-form"><label>Target role<select id="roadmapRole"></select></label><label>Skills you already have<input name="skills" placeholder="e.g. Python, Excel, SQL"></label><label>Weeks available<input name="weeks" type="number" min="1" max="52" value="8"></label><button class="btn btn-primary" type="submit">Create roadmap →</button></form><div id="roadmapResult" class="sb-result"></div><button id="historyButton" class="btn btn-outline" type="button">View saved assessments</button><div id="historyResult" class="sb-result"></div></div></div></div>`;
  const anchor = $('.dashboard-section');
anchor?.after(section);
  loadRolesInto($('#resumeRole')); loadRolesInto($('#roadmapRole'));
  $('#resumeForm').addEventListener('submit',submitResume);
  $('#roadmapForm').addEventListener('submit',submitRoadmap);
  $('#historyButton').addEventListener('click',loadHistory);
}

async function submitResume(e){
  e.preventDefault(); const form=new FormData(e.target); form.append('target_role',$('#resumeRole').value); const out=$('#resumeResult'); out.innerHTML='<div class="sb-loading">Reading résumé…</div>';
  try{const d=await api('/resume-analysis',{method:'POST',body:form});out.innerHTML=`<h3>Recognized skills</h3><p>${d.detected_skills.length?d.detected_skills.map(escapeHtml).join(' · '):'No matching skills were found.'}</p><h3>Priority gaps for ${escapeHtml(d.target_role)}</h3>${d.skill_gaps.map(g=>`<div class="sb-gap"><span>${escapeHtml(g.skill)}</span><b>${g.demand_percent}% demand</b></div>`).join('')||'<p>No major gaps found.</p>'}`;}catch(err){out.innerHTML='<div class="sb-error">Could not read this résumé. Upload a text-based PDF, DOCX, or TXT file.</div>';}
}

async function submitRoadmap(e){
  e.preventDefault();const form=new FormData(e.target);const out=$('#roadmapResult');out.innerHTML='<div class="sb-loading">Creating your roadmap…</div>';
  try{const d=await api('/learning-path',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target_role:$('#roadmapRole').value,current_skills:String(form.get('skills')).split(',').map(s=>s.trim()).filter(Boolean),weeks_available:Number(form.get('weeks'))})});out.innerHTML=d.steps.length?d.steps.map(s=>`<div class="sb-course"><div><strong>Weeks ${s.week_start}–${s.week_end}: ${escapeHtml(s.focus_skill)}</strong><small>${escapeHtml(s.why)}${s.course?` ${escapeHtml(s.course.title)} · ${escapeHtml(s.course.organization)}`:''}</small></div></div>`).join(''):'<p>Your current skills already cover the strongest signals in this sample.</p>';}catch(err){out.innerHTML='<div class="sb-error">Could not create a roadmap. Make sure the backend is running.</div>';}
}

async function loadHistory(){
  const out=$('#historyResult');if(!localStorage.getItem('skillbridge_token')){openAccount();return;}out.innerHTML='<div class="sb-loading">Loading saved assessments…</div>';
  try{const items=await api('/profile/assessments');out.innerHTML=items.length?items.map(x=>`<div class="sb-course"><div><strong>${escapeHtml(x.target_role)} · ${x.readiness_score}% ready</strong><small>${new Date(x.created_at).toLocaleDateString()} · ${x.placement_probability}% estimate</small></div></div>`).join(''):'<p>No saved assessments yet.</p>';}catch(err){out.innerHTML='<div class="sb-error">Your session expired. Please sign in again.</div>';localStorage.removeItem('skillbridge_token');}
}

function wireButtons(){
  const actions=[...$$('button')].filter(b=>/Start Skill Assessment|Start Your Skill Assessment|Get Started|Explore Platform|Skill Assessment/i.test(b.textContent));
  actions.forEach(b=>b.addEventListener('click', ()=>{
    if(/Explore Platform/i.test(b.textContent)){ $('#liveIntelligence')?.scrollIntoView({behavior:'smooth'}); }
    else openAssessment();
  }));
  [...$$('button')].filter(b=>/Log In/i.test(b.textContent)).forEach(b=>b.addEventListener('click',openAccount));
}

window.addEventListener('DOMContentLoaded',()=>{
  addCareerTools();
  wireButtons();
  loadOverview();
});
