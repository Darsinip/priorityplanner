# app.py
from flask import Flask, jsonify, request, render_template_string
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from dateutil import parser as dateparser
import uuid, heapq, json
from typing import Optional, List, Any, Dict

app = Flask(__name__, static_folder="")

# -----------------------
# Models
# -----------------------
@dataclass(order=True)
class Task:
    sort_index: Any = field(init=False, repr=False)
    priority: int = 5  # lower number = higher priority
    deadline: Optional[datetime] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    description: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    completed: bool = False
    progress: int = 0  # 0-100
    dependencies: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    estimated_minutes: Optional[int] = None
    auto_assigned: bool = False
    reminded: bool = False  # server-side "reminder already sent" flag

    def __post_init__(self):
        deadline_ts = self.deadline.timestamp() if self.deadline else float("inf")
        self.sort_index = (self.priority, deadline_ts)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "created_at": self.created_at.isoformat(),
            "completed": self.completed,
            "progress": self.progress,
            "dependencies": self.dependencies,
            "tags": self.tags,
            "estimated_minutes": self.estimated_minutes,
            "auto_assigned": self.auto_assigned,
            "reminded": self.reminded,
        }

    @staticmethod
    def from_dict(d: Dict) -> "Task":
        deadline = dateparser.parse(d["deadline"]) if d.get("deadline") else None
        t = Task(
            priority=d.get("priority", 5),
            deadline=deadline,
            id=d.get("id", str(uuid.uuid4())),
            title=d.get("title", ""),
            description=d.get("description", ""),
            created_at=dateparser.parse(d["created_at"]) if d.get("created_at") else datetime.utcnow(),
            completed=d.get("completed", False),
            progress=d.get("progress", 0),
            dependencies=d.get("dependencies", []),
            tags=d.get("tags", []),
            estimated_minutes=d.get("estimated_minutes"),
            auto_assigned=d.get("auto_assigned", False),
        )
        t.reminded = d.get("reminded", False)
        t.__post_init__()
        return t

# -----------------------
# Task Manager
# -----------------------
class TaskManager:
    def __init__(self):
        self.tasks: Dict[str, Task] = {}
        self.heap: List = []

    def _push_heap(self, task: Task):
        task.__post_init__()
        heapq.heappush(self.heap, (task.sort_index, task.id))

    def _rebuild_heap(self):
        self.heap = []
        for t in self.tasks.values():
            if not t.completed:
                self._push_heap(t)

    def auto_assign_priority_and_deadline(self, title: str, description: str, user_deadline: Optional[datetime] = None):
        # Simple heuristic: keywords + deadline proximity -> priority
        text = f"{title} {description}".lower()
        tags = []
        priority = 5
        if any(k in text for k in ["urgent", "asap", "immediately"]):
            priority = 1
            tags.append("urgent")
        elif "high" in text or "important" in text:
            priority = min(priority, 2)
            tags.append("high")
        elif "low" in text or "whenever" in text:
            priority = max(priority, 7)
            tags.append("low")

        deadline = user_deadline
        if deadline:
            delta = deadline - datetime.utcnow()
            if delta <= timedelta(hours=12):
                priority = min(priority, 1)
                tags.append("due_12h")
            elif delta <= timedelta(hours=24):
                priority = min(priority, 2)
                tags.append("due_24h")
            elif delta <= timedelta(days=3):
                priority = min(priority, 3)
                tags.append("due_3d")

        words = len(description.split())
        estimated_minutes = max(15, min(8 * (words // 20 + 1), 8 * 10))
        return priority, deadline, tags, estimated_minutes

    # Very small local "AI scheduler" heuristic: suggest re-prioritization & order
    def ai_schedule(self):
        # Rebuild heap based on combined score: priority + deadline urgency
        now = datetime.utcnow()
        score_list = []
        for t in self.tasks.values():
            if t.completed:
                continue
            deadline_score = 0
            if t.deadline:
                delta = (t.deadline - now).total_seconds()
                # tasks overdue or very near get bonus (lower number better)
                if delta <= 0:
                    deadline_score = -1000
                else:
                    # closer deadline -> negative smaller -> better
                    deadline_score = delta / (60 * 60)  # hours
            score = t.priority + (deadline_score / 24.0)  # normalize
            score_list.append((score, t))
        # Sort by score ascending
        score_list.sort(key=lambda x: x[0])
        # Return ordered list of ids as suggested schedule
        return [t.id for _, t in score_list]

    def add_task(self, title: str, description: str = "", deadline_iso: Optional[str] = None, priority: Optional[int] = None, deps: Optional[List[str]] = None, from_nlp: bool = False, tags: Optional[List[str]] = None, minutes: Optional[int] = None) -> Task:
        # NLP stub
        if from_nlp:
            parsed = self.ai_parse_task(title if not description else f"{title} {description}")
            title = parsed["title"]
            description = parsed["description"]
            if parsed.get("deadline") and not deadline_iso:
                deadline_iso = parsed["deadline"]
            if parsed.get("priority_hint") and priority is None:
                if parsed["priority_hint"] == "urgent":
                    priority = 1

        deadline = dateparser.parse(deadline_iso) if deadline_iso else None

        if priority is None:
            priority, deadline_auto, auto_tags, estimated_minutes = self.auto_assign_priority_and_deadline(title, description, deadline)
            if not deadline and deadline_auto:
                deadline = deadline_auto
            auto_assigned = True
        else:
            auto_tags = tags or []
            estimated_minutes = minutes
            auto_assigned = False

        t = Task(priority=priority, deadline=deadline, title=title, description=description, tags=auto_tags, estimated_minutes=estimated_minutes, auto_assigned=auto_assigned)
        self.tasks[t.id] = t
        self._push_heap(t)
        if deps:
            for dep_id in deps:
                if dep_id not in t.dependencies:
                    t.dependencies.append(dep_id)
        return t

    # AI stub — naive
    def ai_parse_task(self, natural_text: str) -> Dict[str, Any]:
        out = {
            "title": natural_text if len(natural_text) <= 60 else natural_text[:57] + "...",
            "description": natural_text,
            "deadline": None,
            "priority_hint": None,
            "dependencies": [],
            "estimated_minutes": None,
        }
        text = natural_text.lower()
        if "tomorrow" in text:
            out["deadline"] = (datetime.utcnow() + timedelta(days=1)).isoformat()
        elif "today" in text:
            out["deadline"] = datetime.utcnow().isoformat()
        if any(k in text for k in ["urgent", "asap", "immediately"]):
            out["priority_hint"] = "urgent"
        return out

    def get_task(self, tid: str) -> Optional[Task]:
        return self.tasks.get(tid)

    def list_tasks(self, include_completed=True) -> List[Task]:
        return list(self.tasks.values()) if include_completed else [t for t in self.tasks.values() if not t.completed]

    def update_task(self, tid: str, **kwargs) -> Task:
        t = self.get_task(tid)
        if not t:
            raise KeyError("Task not found")
        # update fields safely
        for k, v in kwargs.items():
            if k == "deadline":
                t.deadline = dateparser.parse(v) if v else None
            elif hasattr(t, k):
                setattr(t, k, v)
        t.__post_init__()
        self._rebuild_heap()
        return t

    def delete_task(self, tid: str):
        if tid in self.tasks:
            del self.tasks[tid]
        self._rebuild_heap()

    def peek_next(self) -> Optional[Task]:
        # top of heap without removal
        while self.heap:
            _, tid = self.heap[0]
            t = self.tasks.get(tid)
            if t is None or t.completed:
                heapq.heappop(self.heap)
                continue
            return t
        return None

    def pop_next(self) -> Optional[Task]:
        while self.heap:
            _, tid = heapq.heappop(self.heap)
            t = self.tasks.get(tid)
            if t and not t.completed:
                return t
        return None

    def set_progress(self, tid: str, progress: int):
        t = self.get_task(tid)
        if not t:
            raise KeyError("Task not found")
        t.progress = max(0, min(100, int(progress)))
        if t.progress == 100:
            t.completed = True
        self._rebuild_heap()

    def complete_task(self, tid: str):
        t = self.get_task(tid)
        if not t:
            raise KeyError("Task not found")
        # check dependencies
        unmet = [d for d in t.dependencies if not self.tasks.get(d) or not self.tasks[d].completed]
        if unmet:
            raise RuntimeError(f"Unmet dependencies: {unmet}")
        t.completed = True
        t.progress = 100
        self._rebuild_heap()

    def export_json(self) -> str:
        all_tasks = [t.to_dict() for t in self.tasks.values()]
        return json.dumps({"tasks": all_tasks}, indent=2)

    def import_json(self, json_str: str):
        data = json.loads(json_str)
        tasks = data.get("tasks", [])
        self.tasks.clear()
        for td in tasks:
            t = Task.from_dict(td)
            self.tasks[t.id] = t
        self._rebuild_heap()

tm = TaskManager()
# demo tasks
tm.add_task("Welcome: Your Priority Planner is ready", "This is a demo task (auto-generated)", priority=5)
tm.add_task("Finish report by tomorrow", "Q3 summary", priority=2, deadline_iso=(datetime.utcnow() + timedelta(days=1)).isoformat())
tm.add_task("Quick call with team", "Discuss milestones", priority=3, deadline_iso=(datetime.utcnow() + timedelta(hours=8)).isoformat())

# -----------------------
# Routes & API
# -----------------------
INDEX_HTML = r"""
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Priority Planner — Upgraded</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <!-- Bootstrap -->
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <!-- Chart.js -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
      body { background: #f5f7fb; padding: 20px; }
      .card-task { border-radius: 12px; transition:transform .12s; }
      .card-task:hover { transform: translateY(-4px); }
      .priority-badge { font-weight:700; }
      .progress { height: 10px; border-radius: 6px; }
      .task-controls button { min-width: 80px; }
      .toast-container { position: fixed; top: 20px; right: 20px; z-index: 1200; }
    </style>
  </head>
  <body>
    <div class="container">
      <div class="d-flex justify-content-between align-items-center mb-3">
        <h3>Priority Planner</h3>
        <div>
          <button class="btn btn-primary me-2" data-bs-toggle="modal" data-bs-target="#createTaskModal">+ New Task</button>
          <button class="btn btn-outline-secondary me-2" onclick="refreshTasks()">Refresh</button>
          <button class="btn btn-outline-info" onclick="exportTasks()">Export</button>
          <button class="btn btn-outline-dark" onclick="autoSchedule()">AI Schedule</button>
        </div>
      </div>

      <div class="row g-3">
        <div class="col-lg-8">
          <div id="task-list"></div>
        </div>

        <div class="col-lg-4">
          <div class="card p-3 mb-3">
            <h5 class="mb-3">Dashboard</h5>
            <canvas id="statusChart" height="150"></canvas>
            <hr>
            <canvas id="priorityChart" height="150"></canvas>
          </div>

          <div class="card p-3">
            <h6>Reminder Settings</h6>
            <div class="mb-2">
              <label class="form-label">Remind when due within (minutes)</label>
              <input id="reminderWindow" type="number" class="form-control" value="60" min="1">
            </div>
            <button class="btn btn-sm btn-outline-primary" onclick="checkReminders()">Check Now</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Create Modal -->
    <div class="modal fade" id="createTaskModal" tabindex="-1">
      <div class="modal-dialog modal-lg">
        <div class="modal-content p-3">
          <div class="modal-header"><h5>Create Task</h5><button class="btn-close" data-bs-dismiss="modal"></button></div>
          <div class="modal-body">
            <form id="taskForm">
              <div class="mb-3">
                <label class="form-label">Title (or natural text)</label>
                <input id="title" class="form-control" placeholder='e.g. "Finish quarterly report by Friday, urgent"'>
              </div>
              <div class="mb-3">
                <label class="form-label">Description</label>
                <textarea id="description" class="form-control" rows="2"></textarea>
              </div>
              <div class="row g-2 mb-3">
                <div class="col">
                  <label class="form-label">Priority</label>
                  <select id="priority" class="form-select">
                    <option value="">Auto</option>
                    <option value="1">1 - Urgent</option>
                    <option value="2">2 - High</option>
                    <option value="3">3 - Medium</option>
                    <option value="5">5 - Low</option>
                    <option value="8">8 - Very Low</option>
                  </select>
                </div>
                <div class="col">
                  <label class="form-label">Deadline</label>
                  <input id="deadline" type="datetime-local" class="form-control">
                </div>
                <div class="col">
                  <label class="form-label">Duration (minutes)</label>
                  <input id="duration" type="number" class="form-control" value="60">
                </div>
              </div>
              <div class="mb-3">
                <label class="form-label">Tags (comma separated)</label>
                <input id="tags" class="form-control">
              </div>
              <div class="mb-3 form-check">
                <input id="from_nlp" type="checkbox" class="form-check-input"><label class="form-check-label">Use AI Assistant (parse natural text)</label>
              </div>
              <div class="text-end">
                <button class="btn btn-secondary" type="button" data-bs-dismiss="modal">Cancel</button>
                <button class="btn btn-primary" type="submit">Create</button>
              </div>
            </form>
            <div id="createResult" class="mt-2"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- Edit Modal -->
    <div class="modal fade" id="editTaskModal" tabindex="-1">
      <div class="modal-dialog modal-lg">
        <div class="modal-content p-3">
          <div class="modal-header"><h5>Edit Task</h5><button class="btn-close" data-bs-dismiss="modal"></button></div>
          <div class="modal-body">
            <form id="editForm">
              <input type="hidden" id="editId">
              <div class="mb-3"><label class="form-label">Title</label><input id="editTitle" class="form-control"></div>
              <div class="mb-3"><label class="form-label">Description</label><textarea id="editDescription" class="form-control"></textarea></div>
              <div class="row g-2 mb-3">
                <div class="col"><label>Priority</label><select id="editPriority" class="form-select"><option value="1">1</option><option value="2">2</option><option value="3">3</option><option value="5">5</option><option value="8">8</option></select></div>
                <div class="col"><label>Deadline</label><input id="editDeadline" type="datetime-local" class="form-control"></div>
                <div class="col"><label>Progress</label><input id="editProgress" type="number" class="form-control" min="0" max="100"></div>
              </div>
              <div class="mb-3"><label>Tags</label><input id="editTags" class="form-control"></div>
              <div class="text-end"><button class="btn btn-secondary" type="button" data-bs-dismiss="modal">Cancel</button><button class="btn btn-primary" type="submit">Save</button></div>
            </form>
            <div id="editResult" class="mt-2"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- Toast container -->
    <div class="toast-container" id="toastContainer"></div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>

<script>
/* ---------- Helpers ---------- */
function badgeColor(priority){
  if(priority<=1) return 'danger';
  if(priority<=2) return 'warning';
  if(priority<=3) return 'info';
  if(priority<=5) return 'secondary';
  return 'light';
}

function toLocalInputValue(isoString){
  if(!isoString) return '';
  const d = new Date(isoString);
  // create local datetime-local value yyyy-MM-ddTHH:mm
  const pad = n => n.toString().padStart(2,'0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function showToast(title, body, type='info', delay=8000){
  const id = 't' + Math.random().toString(36).slice(2,9);
  const container = document.getElementById('toastContainer');
  const div = document.createElement('div');
  div.className = `toast align-items-center text-bg-${type} border-0`;
  div.id = id;
  div.setAttribute('role','alert');
  div.setAttribute('aria-live','assertive');
  div.setAttribute('aria-atomic','true');
  div.innerHTML = `<div class="d-flex"><div class="toast-body"><strong>${title}</strong><div>${body}</div></div><button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button></div>`;
  container.appendChild(div);
  const t = new bootstrap.Toast(div, { delay });
  t.show();
  setTimeout(()=>{ try{div.remove()}catch(e){} }, delay+200);
}

/* ---------- Data/UI ---------- */
let statusChart = null;
let priorityChart = null;

async function refreshCharts(tasks){
  const total = tasks.length;
  const completed = tasks.filter(t=>t.completed).length;
  const inProgress = tasks.filter(t=>t.progress>0 && t.progress<100).length;
  const overdue = tasks.filter(t=>t.deadline && new Date(t.deadline) < new Date() && !t.completed).length;

  // status chart
  const ctx = document.getElementById('statusChart').getContext('2d');
  if(statusChart) statusChart.destroy();
  statusChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: ['Completed','In Progress','Open','Overdue'],
      datasets: [{ data: [completed, inProgress, Math.max(0,total-completed-inProgress-overdue), overdue], backgroundColor: ['#198754','#ffc107','#6c757d','#dc3545'] }]
    },
    options: { plugins:{legend:{position:'bottom'}} }
  });

  // priority chart
  const pCounts = {};
  tasks.forEach(t=> pCounts[t.priority] = (pCounts[t.priority]||0)+1);
  const labels = Object.keys(pCounts).sort((a,b)=>parseInt(a)-parseInt(b));
  const counts = labels.map(l=>pCounts[l]);
  const ctx2 = document.getElementById('priorityChart').getContext('2d');
  if(priorityChart) priorityChart.destroy();
  priorityChart = new Chart(ctx2, {
    type: 'bar',
    data: { labels, datasets: [{ label: 'Tasks by priority', data: counts, backgroundColor: labels.map(l=>l<=2? '#dc3545': l<=3? '#ffc107':'#6c757d') }] },
    options: { plugins:{legend:{display:false}} }
  });
}

/* ---------- Main refresh ---------- */
async function refreshTasks(){
  try {
    const res = await fetch('/api/tasks');
    const j = await res.json();
    const tasks = j.tasks || [];
    const container = document.getElementById('task-list');
    container.innerHTML = '';
    // sort by priority then deadline
    tasks.sort((a,b)=>{
      if(a.priority !== b.priority) return a.priority - b.priority;
      const da = a.deadline?new Date(a.deadline).getTime():Infinity;
      const db = b.deadline?new Date(b.deadline).getTime():Infinity;
      return da - db;
    });

    for(const t of tasks){
      const card = document.createElement('div');
      card.className = 'card card-task p-3 mb-2 shadow-sm';
      const title = t.title || '(no title)';
      const desc = t.description ? `<div class="text-muted small mb-1">${t.description}</div>` : '';
      const deadlineStr = t.deadline ? new Date(t.deadline).toLocaleString() : '';
      const tags = t.tags && t.tags.length ? t.tags.join(', ') : '-';
      const deps = t.dependencies && t.dependencies.length ? t.dependencies.join(', ') : '-';
      const pb = `<div class="progress mt-2"><div class="progress-bar" role="progressbar" style="width:${t.progress}%" aria-valuenow="${t.progress}"></div></div>`;
      card.innerHTML = `
        <div class="d-flex justify-content-between">
          <div style="max-width:65%;">
            <div class="d-flex align-items-start gap-2">
              <div><span class="badge bg-${badgeColor(t.priority)} priority-badge">${t.priority}</span></div>
              <div>
                <strong>${title}</strong>
                ${desc}
                <div class="small text-muted">Tags: ${tags} &nbsp; • &nbsp; Deps: ${deps}</div>
                ${pb}
              </div>
            </div>
          </div>
          <div class="text-end task-controls">
            <div class="small text-muted">${deadlineStr}</div>
            <div class="mt-2 d-flex flex-column gap-1">
              <button class="btn btn-sm btn-outline-primary" onclick="openEdit('${t.id}')">Edit</button>
              <button class="btn btn-sm btn-success" onclick="completeTask('${t.id}')">${t.completed ? 'Completed' : 'Complete'}</button>
              <button class="btn btn-sm btn-outline-secondary" onclick="askSetProgress('${t.id}')">Set Progress</button>
              <button class="btn btn-sm btn-danger" onclick="deleteTask('${t.id}')">Delete</button>
            </div>
          </div>
        </div>
      `;
      container.appendChild(card);
    }
    renderNotificationIfNeeded(tasks);
    refreshCharts(tasks);
  } catch (e){
    console.error('Failed to refresh tasks', e);
  }
}

/* ---------- CRUD & actions ---------- */
document.getElementById('taskForm').addEventListener('submit', async function(e){
  e.preventDefault();
  const title = document.getElementById('title').value.trim();
  if(!title){ document.getElementById('createResult').innerHTML = '<div class="alert alert-danger">Title required</div>'; return;}
  const description = document.getElementById('description').value.trim();
  const priority = document.getElementById('priority').value;
  const deadline = document.getElementById('deadline').value;
  const tags = (document.getElementById('tags').value||'').split(',').map(s=>s.trim()).filter(Boolean);
  const from_nlp = document.getElementById('from_nlp').checked;
  const payload = { title, description, priority: priority?parseInt(priority):null, deadline: deadline||null, tags, from_nlp };
  const res = await fetch('/api/tasks', { method:'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload)});
  const r = await res.json();
  if(r.ok){
    document.getElementById('createResult').innerHTML = '<div class="alert alert-success">Task created</div>';
    setTimeout(()=>{ bootstrap.Modal.getInstance(document.getElementById('createTaskModal')).hide(); document.getElementById('taskForm').reset(); document.getElementById('createResult').innerHTML=''; }, 600);
    refreshTasks();
  } else {
    document.getElementById('createResult').innerHTML = `<div class="alert alert-danger">${r.error||'Error'}</div>`;
  }
});

async function openEdit(id){
  const res = await fetch('/api/tasks');
  const j = await res.json();
  const t = (j.tasks || []).find(x=>x.id===id);
  if(!t) return alert('Task not found');
  document.getElementById('editId').value = t.id;
  document.getElementById('editTitle').value = t.title;
  document.getElementById('editDescription').value = t.description;
  document.getElementById('editPriority').value = t.priority;
  document.getElementById('editDeadline').value = toLocalInputValue(t.deadline);
  document.getElementById('editProgress').value = t.progress;
  document.getElementById('editTags').value = t.tags.join(', ');
  const editModal = new bootstrap.Modal(document.getElementById('editTaskModal'));
  editModal.show();
}

document.getElementById('editForm').addEventListener('submit', async function(e){
  e.preventDefault();
  const id = document.getElementById('editId').value;
  const title = document.getElementById('editTitle').value.trim();
  const description = document.getElementById('editDescription').value.trim();
  const priority = parseInt(document.getElementById('editPriority').value);
  const deadline = document.getElementById('editDeadline').value || null;
  const progress = parseInt(document.getElementById('editProgress').value) || 0;
  const tags = (document.getElementById('editTags').value||'').split(',').map(s=>s.trim()).filter(Boolean);
  const payload = { title, description, priority, deadline, progress, tags };
  const res = await fetch('/api/task/' + id, { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
  const r = await res.json();
  if(r.ok){
    document.getElementById('editResult').innerHTML = '<div class="alert alert-success">Saved</div>';
    setTimeout(()=>{ bootstrap.Modal.getInstance(document.getElementById('editTaskModal')).hide(); document.getElementById('editResult').innerHTML=''; }, 600);
    refreshTasks();
  } else {
    document.getElementById('editResult').innerHTML = `<div class="alert alert-danger">${r.error||'Error'}</div>`;
  }
});

async function completeTask(id){
  const res = await fetch('/api/complete', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ id })});
  const r = await res.json();
  if(r.ok){ showToast('Task completed', '', 'success'); refreshTasks(); } else showToast('Error', r.error||'Could not complete', 'danger');
}

async function deleteTask(id){
  if(!confirm('Delete task? This cannot be undone.')) return;
  const res = await fetch('/api/task/' + id, { method:'DELETE' });
  const r = await res.json();
  if(r.ok) { showToast('Deleted', '', 'secondary'); refreshTasks(); } else showToast('Error', r.error||'Delete failed', 'danger');
}

async function askSetProgress(id){
  const v = prompt('Enter progress 0-100', '100'); if(v===null) return;
  const progress = parseInt(v);
  if(isNaN(progress)) return alert('Invalid number');
  const res = await fetch('/api/progress', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ id, progress })});
  const r = await res.json();
  if(r.ok){ showToast('Progress updated','', 'info'); refreshTasks(); } else showToast('Error', r.error||'Could not set progress', 'danger');
}

/* ---------- Export/Import ---------- */
async function exportTasks(){
  const res = await fetch('/api/export');
  const txt = await res.text();
  const blob = new Blob([txt], {type: 'application/json'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href = url; a.download = 'tasks_export.json'; a.click();
}

/* ---------- Reminders ---------- */
function renderNotificationIfNeeded(tasks){
  // client-side quick check (server also tracks reminded flag)
  // nothing here; checkReminders has logic
}

async function checkReminders(){
  const res = await fetch('/api/tasks'); const j = await res.json(); const tasks = j.tasks || [];
  const minutes = parseInt(document.getElementById('reminderWindow').value) || 60;
  const now = new Date();
  for(const t of tasks){
    if(t.completed) continue;
    if(t.reminded) continue; // server-side indicated already reminded
    if(!t.deadline) continue;
    const d = new Date(t.deadline);
    const diffMin = (d - now) / (60*1000);
    if(diffMin <= minutes && diffMin >= -60){ // within window or just passed (not >1hr late)
      // show toast and mark on server as reminded
      showToast('Reminder: ' + (t.title||'Task'), `Due at ${d.toLocaleString()}`, 'warning', 15000);
      // mark server-side so we don't remind repeatedly
      fetch('/api/mark_reminded', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ id: t.id })});
    }
  }
}

// run periodic check
setInterval(checkReminders, 60 * 1000); // every 60s
// also run at load
setTimeout(checkReminders, 1500);

/* ---------- AI Scheduler ---------- */
async function autoSchedule(){
  const res = await fetch('/api/ai_schedule');
  const j = await res.json();
  if(j.ok){
    // j.order is array of task ids in suggested order
    let msg = 'Suggested order (top first):\n' + j.order.slice(0,10).map((id,i)=>`${i+1}. ${ (j.map[id]||'') }`).join('\n');
    showToast('AI Schedule suggested', msg.replace(/\n/g,'<br>'), 'info', 10000);
  } else {
    showToast('AI Schedule error', j.error||'Error', 'danger');
  }
}

/* ---------- Init ---------- */
refreshTasks();
</script>
  </body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(INDEX_HTML)

# API: list tasks
@app.route("/api/tasks", methods=["GET"])
def api_list_tasks():
    tasks = [t.to_dict() for t in tm.list_tasks(include_completed=True)]
    # provide map for id->title to help UI show suggested names
    id_map = {t.id: t.title for t in tm.list_tasks(include_completed=False)}
    return jsonify({"tasks": tasks, "ok": True, "map": id_map})

# API: add task
@app.route("/api/tasks", methods=["POST"])
def api_create_task():
    data = request.json or {}
    title = data.get("title", "").strip()
    description = data.get("description", "").strip()
    priority = data.get("priority", None)
    deadline = data.get("deadline", None)
    deps = data.get("deps", None)
    tags = data.get("tags", [])
    minutes = data.get("minutes", None)
    from_nlp = data.get("from_nlp", False)
    if not title:
        return jsonify({"ok": False, "error": "Title required"}), 400
    try:
        t = tm.add_task(title=title, description=description, deadline_iso=deadline, priority=priority, deps=deps, from_nlp=from_nlp, tags=tags, minutes=minutes)
        return jsonify({"ok": True, "task": t.to_dict()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# API: update task
@app.route("/api/task/<tid>", methods=["PUT"])
def api_update_task(tid):
    data = request.json or {}
    try:
        updated = tm.update_task(tid,
                                 title=data.get("title"),
                                 description=data.get("description"),
                                 priority=data.get("priority"),
                                 deadline=data.get("deadline"),
                                 progress=data.get("progress"),
                                 tags=data.get("tags"))
        return jsonify({"ok": True, "task": updated.to_dict()})
    except KeyError:
        return jsonify({"ok": False, "error": "Task not found"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

# API: delete
@app.route("/api/task/<tid>", methods=["DELETE"])
def api_delete_task(tid):
    try:
        tm.delete_task(tid)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

# API: complete task
@app.route("/api/complete", methods=["POST"])
def api_complete():
    data = request.json or {}
    tid = data.get("id")
    if not tid:
        return jsonify({"ok": False, "error": "id required"}), 400
    try:
        tm.complete_task(tid)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

# API: set progress
@app.route("/api/progress", methods=["POST"])
def api_progress():
    data = request.json or {}
    tid = data.get("id")
    progress = data.get("progress")
    if tid is None or progress is None:
        return jsonify({"ok": False, "error": "id and progress required"}), 400
    try:
        tm.set_progress(tid, progress)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

# API: mark reminded (so server doesn't re-notify)
@app.route("/api/mark_reminded", methods=["POST"])
def api_mark_reminded():
    data = request.json or {}
    tid = data.get("id")
    if not tid:
        return jsonify({"ok": False, "error": "id required"}), 400
    t = tm.get_task(tid)
    if not t:
        return jsonify({"ok": False, "error": "not found"}), 404
    t.reminded = True
    return jsonify({"ok": True})

# API: AI schedule
@app.route("/api/ai_schedule", methods=["GET"])
def api_ai_schedule():
    try:
        order = tm.ai_schedule()
        # provide title map too
        amap = {tid: tm.tasks[tid].title for tid in order}
        return jsonify({"ok": True, "order": order, "map": amap})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# export/import
@app.route("/api/export", methods=["GET"])
def api_export():
    return tm.export_json(), 200, {"Content-Type": "application/json"}

@app.route("/api/import", methods=["POST"])
def api_import():
    if 'file' in request.files:
        txt = request.files['file'].read().decode('utf-8')
    else:
        txt = request.data.decode('utf-8')
    try:
        tm.import_json(txt)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

# API: update task
@app.route("/api/task/<tid>", methods=["PUT"])
def api_update_task_route(tid):  # ← changed name
    data = request.json or {}
    try:
        t = tm.update_task(tid, **data)
        return jsonify({"ok": True, "task": t.to_dict()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

# API: delete task
@app.route("/api/task/<tid>", methods=["DELETE"])
def api_delete_task_route(tid):  # ← unique name
    try:
        tm.delete_task(tid)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

# API: mark progress
@app.route("/api/progress", methods=["POST"])
def api_set_progress_route():  # ← unique name
    data = request.json or {}
    tid = data.get("id")
    progress = data.get("progress", 0)
    try:
        tm.set_progress(tid, progress)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

# API: complete task
@app.route("/api/complete", methods=["POST"])
def api_complete_route():  # ← unique name
    data = request.json or {}
    tid = data.get("id")
    try:
        tm.complete_task(tid)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

# API: export tasks
@app.route("/api/export", methods=["GET"])
def api_export_route():  # ← unique name
    try:
        txt = tm.export_json()
        return txt, 200, {"Content-Type": "application/json"}
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

# API: AI schedule
@app.route("/api/ai_schedule", methods=["GET"])
def api_ai_schedule_route():  # ← unique name
    try:
        order = tm.ai_schedule()
        id_map = {t.id: t.title for t in tm.list_tasks()}
        return jsonify({"ok": True, "order": order, "map": id_map})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

# API: mark reminded
@app.route("/api/mark_reminded", methods=["POST"])
def api_mark_reminded_route():  # ← unique name
    data = request.json or {}
    tid = data.get("id")
    t = tm.get_task(tid)
    if not t:
        return jsonify({"ok": False, "error": "Task not found"}), 404
    t.reminded = True
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(debug=True)
