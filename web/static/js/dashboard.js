// Lightweight vanilla-JS handlers for the review dashboard. Optimistic UI; all
// state writes go to the Flask /api/* endpoints.

function flash(msg) {
  let el = document.querySelector('.flash');
  if (!el) { el = document.createElement('div'); el.className = 'flash'; document.body.appendChild(el); }
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), 1400);
}

async function post(url, body) {
  const r = await fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) { flash('Error: ' + r.status); throw new Error(r.status); }
  return r.json();
}

function jobIdOf(el) {
  const host = el.closest('[data-job-id]');
  return host ? host.getAttribute('data-job-id') : null;
}

document.addEventListener('click', async (e) => {
  const btn = e.target.closest('button');
  if (!btn) return;

  // rate relevant / pass / too-senior (toggle). data-reason qualifies a 'down':
  // 'qualification' (too senior) won't penalize similar junior roles in ranking.
  if (btn.dataset.rate) {
    const jobId = jobIdOf(btn);
    const already = btn.classList.contains('on');
    const rating = already ? 'clear' : btn.dataset.rate;
    const reason = btn.dataset.reason || null;
    // clear siblings within the same control group
    btn.closest('.actions, .rate-cell')
       .querySelectorAll('button.rate').forEach(b => b.classList.remove('on'));
    if (!already) btn.classList.add('on');
    await post('/api/rate', { job_id: jobId, rating, reason });
    const label = already ? 'rating cleared'
      : (reason === 'qualification' ? 'passed — too senior' : 'rated ' + rating);
    flash(label);
    return;
  }

  // mark applied
  if (btn.hasAttribute('data-apply')) {
    const jobId = jobIdOf(btn);
    await post('/api/apply', { job_id: jobId });
    btn.classList.add('on'); btn.textContent = 'Applied';
    flash('marked applied');
    return;
  }

  // proposal accept / dismiss
  if (btn.dataset.action) {
    const card = btn.closest('[data-proposal-id]');
    const pid = card.getAttribute('data-proposal-id');
    await post('/api/proposal', { proposal_id: pid, action: btn.dataset.action });
    card.style.opacity = .4; card.querySelectorAll('button').forEach(b => b.disabled = true);
    flash('proposal ' + btn.dataset.action + 'ed');
    return;
  }
});

// status dropdown
document.addEventListener('change', async (e) => {
  const sel = e.target.closest('select.status');
  if (!sel) return;
  await post('/api/status', { job_id: jobIdOf(sel), status: sel.value });
  flash('status → ' + sel.value);
});
