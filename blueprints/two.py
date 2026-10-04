"""
TWO blueprint — technical work orders
"""
from datetime import datetime, timedelta

from flask import (Blueprint, request, redirect, url_for, flash, render_template,
                   jsonify, send_file, current_app)
from flask_login import login_required, current_user
from flask_babel import gettext as _

from models import (db, TechnicalWorkOrder, TWOChecklistItem, TWOSignature, TWOAssignment,
                    two_workers, Monteur, Machine, FactorySection, User, FaultReport,
                    WorkSchedule, WeekendShift, Vacation, MaintenancePlan, PartMaintenanceLog,
                    ResponsibleGroup, TWOPhoto, now_local)
from utils import (role_required, safe_commit, safe_int, safe_float, safe_date,
                   is_work_date, WORK_SHIFT_TYPES, OFF_SHIFT_TYPES, get_day_shift,
                   get_belgian_holidays, save_uploaded_file, log_audit)

bp = Blueprint('two', __name__)


def gen_two_number():
    from utils import next_number_suffix
    day = now_local().strftime('%Y%m%d')
    num = next_number_suffix(TechnicalWorkOrder, 'number', f'TWO-{day}')
    return f'TWO-{day}-{num:04d}'

@bp.route('/two')
@login_required
@role_required('admin', 'director', 'technician')
def two_list():
    if current_user.has_role('admin', 'director'):
        orders = TechnicalWorkOrder.query.order_by(TechnicalWorkOrder.created_at.desc()).all()
    elif current_user.has_role('technician'):
        orders = TechnicalWorkOrder.query.order_by(TechnicalWorkOrder.created_at.desc()).all()
    else:
        orders = TechnicalWorkOrder.query.filter_by(created_by=current_user.id).order_by(TechnicalWorkOrder.created_at.desc()).all()

    # Группировка по году → месяцу (planned_date, иначе created_at).
    # Пустые месяцы не попадают в список.
    from collections import OrderedDict
    MONTHS_RU = {1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель', 5: 'Май', 6: 'Июнь',
                 7: 'Июль', 8: 'Август', 9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь'}
    groups = OrderedDict()  # year -> OrderedDict(month_num -> {'label':..., 'orders':[...]})
    for o in orders:
        dt = o.planned_date or (o.created_at.date() if o.created_at else None)
        if not dt:
            key = (None, None)
            year, month = None, None
        else:
            year, month = dt.year, dt.month
        if year not in groups:
            groups[year] = OrderedDict()
        if month not in groups[year]:
            groups[year][month] = {'label': MONTHS_RU.get(month, '—') if month else _('No date'), 'orders': []}
        groups[year][month]['orders'].append(o)

    return render_template('two_list.html', orders=orders, groups=groups)

@bp.route('/two/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def two_new():
    if request.method == 'POST':
        fault_id = safe_int(request.form.get('fault_id')) or None
        two = TechnicalWorkOrder(
            number=gen_two_number(),
            fault_id=fault_id,
            machine_id=safe_int(request.form.get('machine_id')) or None,
            section_id=safe_int(request.form.get('section_id')) or None,
            description=request.form['description'],
            additional_work=request.form.get('additional_work', ''),
            planned_date=(d := safe_date(request.form.get('planned_date'))) and d.date() or None,
            status=request.form.get('status', 'draft'),
            notes=request.form.get('notes', ''),
            created_by=current_user.id
        )
        # Assign workers
        worker_ids = request.form.getlist('worker_ids')
        for wid in worker_ids:
            w = Monteur.query.get(int(wid))
            if w:
                two.workers.append(w)
        db.session.add(two)
        db.session.flush()

        # Process section assignments
        section_ids = request.form.getlist('section_ids')
        section_descs = request.form.getlist('section_descriptions')
        for i, sid in enumerate(section_ids):
            work_items = request.form.getlist(f'section_work_{i}')
            if not sid:
                # Отдел не выбран — сохраняем работы в общий чек-лист, не теряем
                for j, text in enumerate(work_items):
                    if text.strip():
                        db.session.add(TWOChecklistItem(
                            two_id=two.id, assignment_id=None,
                            text=text.strip(), sort_order=j
                        ))
                continue
            assignment = TWOAssignment(
                two_id=two.id,
                section_id=int(sid),
                description=section_descs[i] if i < len(section_descs) else '',
                sort_order=i
            )
            db.session.add(assignment)
            db.session.flush()
            # Add checklist items for this section
            for j, text in enumerate(work_items):
                if text.strip():
                    db.session.add(TWOChecklistItem(
                        two_id=two.id, assignment_id=assignment.id,
                        text=text.strip(), sort_order=j
                    ))

        # Process machine assignments
        machine_ids = request.form.getlist('machine_ids')
        machine_descs = request.form.getlist('machine_descriptions')
        for i, mid in enumerate(machine_ids):
            work_items = request.form.getlist(f'machine_work_{i}')
            if not mid:
                for j, text in enumerate(work_items):
                    if text.strip():
                        db.session.add(TWOChecklistItem(
                            two_id=two.id, assignment_id=None,
                            text=text.strip(), sort_order=j
                        ))
                continue
            assignment = TWOAssignment(
                two_id=two.id,
                machine_id=int(mid),
                description=machine_descs[i] if i < len(machine_descs) else '',
                sort_order=i
            )
            db.session.add(assignment)
            db.session.flush()
            # Add checklist items for this machine
            for j, text in enumerate(work_items):
                if text.strip():
                    db.session.add(TWOChecklistItem(
                        two_id=two.id, assignment_id=assignment.id,
                        text=text.strip(), sort_order=j
                    ))

        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(request.referrer or '/')
        # Handle photos
        if 'photos' in request.files:
            saved = 0
            for photo in request.files.getlist('photos'):
                if photo.filename:
                    fn = save_uploaded_file(photo, prefix=f"two_{two.id}_")
                    if fn:
                        db.session.add(TWOPhoto(two_id=two.id, filename=fn))
                        saved += 1
                    else:
                        flash(_('File type not allowed') + f': {photo.filename}', 'error')
            if saved and not safe_commit():
                flash(_('Save failed'), 'error')
                return redirect(url_for('two.two_detail', two_id=two.id))
        log_audit('create', 'two', two.id, two.number)
        flash(_('TWO created') + f': {two.number}', 'success')
        return redirect(url_for('two.two_detail', two_id=two.id))
    faults = FaultReport.query.filter(FaultReport.status.in_(['open', 'accepted', 'in_progress'])).order_by(FaultReport.created_at.desc()).all()
    # Only workers from technical service (Technician group or linked user with technician role)
    workers = Monteur.query.filter_by(actief=True).filter(
        (Monteur.group_id != None) & (Monteur.group_id.in_(
            db.session.query(ResponsibleGroup.id).filter_by(access_level='technician')
        )) | (Monteur.user_id != None) & (Monteur.user_id.in_(
            db.session.query(User.id).filter_by(role='technician', is_active_user=True)
        ))
    ).order_by(Monteur.naam).all()
    machines = Machine.query.order_by(Machine.name).all()
    sections = FactorySection.query.order_by(FactorySection.name).all()
    return render_template('two_form.html', two=None, faults=faults, workers=workers, machines=machines, sections=sections)

@bp.route('/api/check-worker-availability')
@login_required
def check_worker_availability():
    """Check if a worker is available on a given date."""
    worker_id = request.args.get('worker_id', type=int)
    date_str = request.args.get('date', '')
    if not worker_id or not date_str:
        return jsonify({'available': True})
    date = datetime.strptime(date_str, '%Y-%m-%d').date()
    
    # Check weekend shifts (off, sick) / working Saturday
    shift = get_day_shift(worker_id, date)
    if shift and shift.shift_type in OFF_SHIFT_TYPES:
        worker = User.query.get(worker_id)
        # Find alternative workers
        alternatives = suggest_available_workers(date, [worker_id])
        next_dates = suggest_available_dates(worker_id, date)
        return jsonify({
            'available': False,
            'reason': 'off' if shift.shift_type == 'off' else 'sick',
            'worker': worker.display_name if worker else '',
            'date': date.strftime('%d-%m-%Y'),
            'alternatives': alternatives,
            'next_dates': next_dates
        })
    # Working weekend (Sat chosen by mechanic / set by head of tech service)
    if shift and shift.shift_type in WORK_SHIFT_TYPES:
        return jsonify({'available': True, 'weekend_work': True, 'shift_type': shift.shift_type})
    
    # Check Belgian holidays
    holidays = get_belgian_holidays(date.year)
    if date in holidays:
        alternatives = suggest_available_workers(date, [worker_id])
        next_dates = suggest_available_dates(worker_id, date)
        return jsonify({
            'available': False,
            'reason': 'holiday',
            'holiday': holidays[date],
            'date': date.strftime('%d-%m-%Y'),
            'alternatives': alternatives,
            'next_dates': next_dates
        })
    
    # Check work schedule - is this a working day for the worker?
    weekday = date.isoweekday()  # 1=Monday, 7=Sunday
    schedule = WorkSchedule.query.filter_by(user_id=worker_id, is_active=True).first()
    if schedule:
        work_days = [int(d.strip()) for d in schedule.work_days.split(',')]
        if weekday not in work_days:
            worker = User.query.get(worker_id)
            alternatives = suggest_available_workers(date, [worker_id])
            next_dates = suggest_available_dates(worker_id, date)
            day_names = {1:'Mon',2:'Tue',3:'Wed',4:'Thu',5:'Fri',6:'Sat',7:'Sun'}
            work_day_names = ', '.join(day_names.get(d, str(d)) for d in work_days)
            return jsonify({
                'available': False,
                'reason': 'not_work_day',
                'worker': worker.display_name if worker else '',
                'date': date.strftime('%d-%m-%Y'),
                'schedule': f"{schedule.shift_start}-{schedule.shift_end}",
                'work_days': work_day_names,
                'alternatives': alternatives,
                'next_dates': next_dates
            })
    else:
        # No schedule = assume Mon-Fri working week
        if weekday >= 6:  # Saturday or Sunday
            worker = User.query.get(worker_id)
            alternatives = suggest_available_workers(date, [worker_id])
            next_dates = suggest_available_dates(worker_id, date)
            return jsonify({
                'available': False,
                'reason': 'weekend',
                'worker': worker.display_name if worker else '',
                'date': date.strftime('%d-%m-%Y'),
                'alternatives': alternatives,
                'next_dates': next_dates
            })
    
    return jsonify({'available': True})

def suggest_available_workers(date, exclude_ids=None):
    """Find workers available on a given date."""
    if exclude_ids is None:
        exclude_ids = []
    
    holidays = get_belgian_holidays(date.year)
    if date in holidays:
        return []
    
    weekday = date.isoweekday()
    workers = Monteur.query.filter_by(actief=True).all()
    if not workers:
        return []
    
    worker_user_ids = [w.user_id for w in workers if w.user_id and w.user_id not in exclude_ids]
    if not worker_user_ids:
        return []
    
    # Batch-fetch shifts (1 query instead of N)
    shifts = WeekendShift.query.filter(
        WeekendShift.user_id.in_(worker_user_ids),
        WeekendShift.date == date
    ).all()
    off_user_ids = {s.user_id for s in shifts if s.shift_type in OFF_SHIFT_TYPES}
    work_shift_user_ids = {s.user_id for s in shifts if s.shift_type in WORK_SHIFT_TYPES}
    
    # Batch-fetch schedules (1 query instead of N)
    schedules = WorkSchedule.query.filter(
        WorkSchedule.user_id.in_(worker_user_ids),
        WorkSchedule.is_active == True
    ).all()
    schedule_map = {s.user_id: s for s in schedules}
    
    available = []
    for w in workers:
        if not w.user_id or w.user_id in exclude_ids:
            continue
        if w.user_id in off_user_ids:
            continue
        # Explicit working Saturday/Sunday (set by head of tech service / admin)
        if w.user_id in work_shift_user_ids:
            available.append({'id': w.user_id, 'name': w.naam, 'specialty': w.specialisatie or ''})
            continue
        
        schedule = schedule_map.get(w.user_id)
        if schedule and schedule.work_days:
            work_days = [int(d.strip()) for d in schedule.work_days.split(',')]
            if weekday not in work_days:
                continue
        else:
            if weekday >= 6:
                continue
        
        available.append({'id': w.user_id, 'name': w.naam, 'specialty': w.specialisatie or ''})
    
    return available[:5]

def suggest_available_dates(worker_id, from_date, count=5):
    """Find next available dates for a worker."""
    dates = []
    end_date = from_date + timedelta(days=31)
    
    # Get holidays for both years (in case we span year boundary)
    holidays = get_belgian_holidays(from_date.year)
    if end_date.year != from_date.year:
        holidays.update(get_belgian_holidays(end_date.year))
    
    # Batch-fetch shifts for the whole range (1 query instead of 30)
    shifts = WeekendShift.query.filter(
        WeekendShift.user_id == worker_id,
        WeekendShift.date > from_date,
        WeekendShift.date <= end_date
    ).all()
    shift_map = {s.date: s for s in shifts}
    
    # Fetch schedule once (constant for this worker)
    schedule = WorkSchedule.query.filter_by(user_id=worker_id, is_active=True).first()
    work_days = None
    if schedule and schedule.work_days:
        work_days = [int(d.strip()) for d in schedule.work_days.split(',')]
    
    current = from_date + timedelta(days=1)
    for _ in range(31):
        if len(dates) >= count:
            break
        
        if current in holidays:
            current += timedelta(days=1)
            continue
        
        shift = shift_map.get(current)
        if shift and shift.shift_type in OFF_SHIFT_TYPES:
            current += timedelta(days=1)
            continue
        # Working Saturday/Sunday (WeekendShift work type) overrides calendar weekend
        if shift and shift.shift_type in WORK_SHIFT_TYPES:
            dates.append(current.strftime('%Y-%m-%d'))
            current += timedelta(days=1)
            continue
        
        weekday = current.isoweekday()
        if work_days:
            if weekday not in work_days:
                current += timedelta(days=1)
                continue
        else:
            if weekday >= 6:
                current += timedelta(days=1)
                continue
        
        dates.append(current.strftime('%Y-%m-%d'))
        current += timedelta(days=1)
    
    return dates

@bp.route('/two/<int:two_id>')
@login_required
@role_required('admin', 'director', 'technician')
def two_detail(two_id):
    two = TechnicalWorkOrder.query.get_or_404(two_id)
    return render_template('two_detail.html', two=two)

@bp.route('/two/<int:two_id>/checklist/add', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def two_checklist_add(two_id):
    two = TechnicalWorkOrder.query.get_or_404(two_id)
    text = request.form.get('text', '').strip()
    if text:
        max_order = max([i.sort_order for i in two.checklist_items], default=0)
        item = TWOChecklistItem(two_id=two_id, text=text, sort_order=max_order + 1)
        db.session.add(item)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('two.two_detail', two_id=two_id))
    return redirect(url_for('two.two_detail', two_id=two_id))

@bp.route('/two/checklist/<int:item_id>/toggle', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def two_checklist_toggle(item_id):
    item = TWOChecklistItem.query.get_or_404(item_id)
    item.is_done = not item.is_done
    item.done_at = now_local() if item.is_done else None
    item.done_by = current_user.id if item.is_done else None
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True, 'is_done': item.is_done, 'done_at': item.done_at.strftime('%d.%m.%Y %H:%M') if item.done_at else None})

@bp.route('/two/checklist/<int:item_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def two_checklist_delete(item_id):
    item = TWOChecklistItem.query.get_or_404(item_id)
    two_id = item.two_id
    db.session.delete(item)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('two.two_detail', two_id=two_id))
    return redirect(url_for('two.two_detail', two_id=two_id))

@bp.route('/two/<int:two_id>/signature', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def two_add_signature(two_id):
    two = TechnicalWorkOrder.query.get_or_404(two_id)
    signer_name = request.form.get('signer_name', '').strip()
    signature_data = request.form.get('signature_data', '')
    if signer_name and signature_data:
        sig = TWOSignature(two_id=two_id, signer_name=signer_name, signature_data=signature_data)
        db.session.add(sig)
        two.status = 'completed'
        two.completed_at = now_local()
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('two.two_detail', two_id=two_id))
        flash(_('Signature saved'), 'success')
    return redirect(url_for('two.two_detail', two_id=two_id))

@bp.route('/two/<int:two_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def two_edit(two_id):
    two = TechnicalWorkOrder.query.get_or_404(two_id)
    from utils import acquire_lock, release_lock
    if request.method == 'POST':
        ok, lock_info = acquire_lock('two', two_id, current_user.id, current_user.username)
        if not ok:
            flash(_('Record is being edited by %(user)s. Try again later.', user=lock_info.get('user_name', '?')), 'error')
            return redirect(url_for('two.two_detail', two_id=two_id))
        two.fault_id = safe_int(request.form.get('fault_id')) or None
        two.machine_id = safe_int(request.form.get('machine_id')) or None
        two.section_id = safe_int(request.form.get('section_id')) or None
        two.description = request.form['description']
        two.additional_work = request.form.get('additional_work', '')
        two.planned_date = (d := safe_date(request.form.get('planned_date'))) and d.date() or None
        two.status = request.form.get('status', two.status)
        two.result = request.form.get('result', '')
        two.parts_used = request.form.get('parts_used', '')
        two.time_spent_hours = safe_float(request.form.get('time_spent_hours'), 0)
        two.notes = request.form.get('notes', '')
        if request.form.get('started_at'):
            try:
                two.started_at = datetime.strptime(request.form['started_at'], '%Y-%m-%dT%H:%M')
            except ValueError:
                flash(_('Invalid start date format'), 'error')
        if request.form.get('completed_at'):
            try:
                two.completed_at = datetime.strptime(request.form['completed_at'], '%Y-%m-%dT%H:%M')
            except ValueError:
                flash(_('Invalid completion date format'), 'error')
        # Update workers
        two.workers = []
        for wid in request.form.getlist('worker_ids'):
            w = Monteur.query.get(int(wid))
            if w:
                two.workers.append(w)
        # Update assignments — delete old, create new
        for a in two.assignments:
            db.session.delete(a)
        db.session.flush()
        # Process section assignments
        section_ids = request.form.getlist('section_ids')
        section_descs = request.form.getlist('section_descriptions')
        for i, sid in enumerate(section_ids):
            work_items = request.form.getlist(f'section_work_{i}')
            if not sid:
                for j, text in enumerate(work_items):
                    if text.strip():
                        db.session.add(TWOChecklistItem(
                            two_id=two.id, assignment_id=None,
                            text=text.strip(), sort_order=j
                        ))
                continue
            assignment = TWOAssignment(
                two_id=two.id,
                section_id=int(sid),
                description=section_descs[i] if i < len(section_descs) else '',
                sort_order=i
            )
            db.session.add(assignment)
            db.session.flush()
            for j, text in enumerate(work_items):
                if text.strip():
                    db.session.add(TWOChecklistItem(
                        two_id=two.id, assignment_id=assignment.id,
                        text=text.strip(), sort_order=j
                    ))
        # Process machine assignments
        machine_ids = request.form.getlist('machine_ids')
        machine_descs = request.form.getlist('machine_descriptions')
        for i, mid in enumerate(machine_ids):
            work_items = request.form.getlist(f'machine_work_{i}')
            if not mid:
                for j, text in enumerate(work_items):
                    if text.strip():
                        db.session.add(TWOChecklistItem(
                            two_id=two.id, assignment_id=None,
                            text=text.strip(), sort_order=j
                        ))
                continue
            assignment = TWOAssignment(
                two_id=two.id,
                machine_id=int(mid),
                description=machine_descs[i] if i < len(machine_descs) else '',
                sort_order=i
            )
            db.session.add(assignment)
            db.session.flush()
            for j, text in enumerate(work_items):
                if text.strip():
                    db.session.add(TWOChecklistItem(
                        two_id=two.id, assignment_id=assignment.id,
                        text=text.strip(), sort_order=j
                    ))
        # Handle photos
        if 'photos' in request.files:
            saved = 0
            for photo in request.files.getlist('photos'):
                if photo.filename:
                    fn = save_uploaded_file(photo, prefix=f"two_{two.id}_")
                    if fn:
                        db.session.add(TWOPhoto(two_id=two.id, filename=fn))
                        saved += 1
                    else:
                        flash(_('File type not allowed') + f': {photo.filename}', 'error')
            if saved and not safe_commit():
                flash(_('Save failed'), 'error')
                return redirect(url_for('two.two_detail', two_id=two.id))
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('two.two_detail', two_id=two.id))
        release_lock('two', two_id, current_user.id)
        log_audit('update', 'two', two.id, two.number)
        flash(_('TWO updated'), 'success')
        return redirect(url_for('two.two_detail', two_id=two.id))
    ok, lock_info = acquire_lock('two', two_id, current_user.id, current_user.username)
    if not ok:
        flash(_('⚠️ This record is being edited by %(user)s (since %(time)s). You cannot edit it now.', user=lock_info.get('user_name', '?'), time=lock_info.get('locked_at', '?')), 'error')
        return redirect(url_for('two.two_detail', two_id=two_id))
    faults = FaultReport.query.filter(FaultReport.status.in_(['open', 'accepted', 'in_progress'])).order_by(FaultReport.created_at.desc()).all()
    # Only workers from technical service (Technician group or linked user with technician role)
    workers = Monteur.query.filter_by(actief=True).filter(
        (Monteur.group_id != None) & (Monteur.group_id.in_(
            db.session.query(ResponsibleGroup.id).filter_by(access_level='technician')
        )) | (Monteur.user_id != None) & (Monteur.user_id.in_(
            db.session.query(User.id).filter_by(role='technician', is_active_user=True)
        ))
    ).order_by(Monteur.naam).all()
    machines = Machine.query.order_by(Machine.name).all()
    sections = FactorySection.query.order_by(FactorySection.name).all()
    return render_template('two_form.html', two=two, faults=faults, workers=workers, machines=machines, sections=sections)

@bp.route('/two/<int:two_id>/complete', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def two_complete(two_id):
    two = TechnicalWorkOrder.query.get_or_404(two_id)
    two.status = 'completed'
    two.completed_at = now_local()
    two.result = request.form.get('result', two.result)
    # Do NOT auto-resolve linked fault — close manually
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('two.two_detail', two_id=two.id))
    log_audit('complete', 'two', two.id, two.number)
    flash(_('TWO completed'), 'success')
    return redirect(url_for('two.two_detail', two_id=two.id))

@bp.route('/two/<int:two_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def two_delete(two_id):
    two = TechnicalWorkOrder.query.get_or_404(two_id)
    db.session.delete(two)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('two.two_list'))
    flash(_('TWO deleted'), 'success')
    return redirect(url_for('two.two_list'))

@bp.route('/two/merge', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def two_merge():
    """Merge multiple TWOs with the same planned_date into one combined work order."""
    two_ids = request.form.getlist('two_ids')
    if not two_ids or len(two_ids) < 2:
        flash(_('Select at least 2 TWOs to merge'), 'error')
        return redirect(url_for('two.two_list'))

    twos = []
    for tid in two_ids:
        t = TechnicalWorkOrder.query.get(int(tid))
        if t:
            twos.append(t)

    if len(twos) < 2:
        flash(_('Not enough valid TWOs to merge'), 'error')
        return redirect(url_for('two.two_list'))

    # Use the first TWO as the target, merge others into it
    target = twos[0]
    merged_descriptions = []
    merged_workers = set()
    merged_assignments = []
    merged_notes = []
    orphan_checklists = []  # checklist items without assignment_id
    earliest_date = target.planned_date

    for two in twos:
        if two.planned_date and (earliest_date is None or two.planned_date < earliest_date):
            earliest_date = two.planned_date
        if two.id != target.id:
            merged_descriptions.append(two.description)
            if two.additional_work:
                merged_descriptions.append(two.additional_work)
            if two.notes:
                merged_notes.append(f'[{two.number}] {two.notes}')
            for w in two.workers:
                merged_workers.add(w.id)
            # Copy assignments (sections + machines)
            for a in two.assignments:
                merged_assignments.append({
                    'section_id': a.section_id,
                    'machine_id': a.machine_id,
                    'description': a.description,
                    'checklist': [ci.text for ci in a.checklist_items]
                })
            # Checklist items without assignment
            for ci in two.checklist_items:
                if ci.assignment_id is None:
                    orphan_checklists.append(ci.text)
            # Transfer photos and signatures to target (before delete cascades them)
            for photo in list(two.photos):
                photo.two_id = target.id
            for sig in list(two.signatures):
                sig.two_id = target.id

    # Merge into target
    if merged_descriptions:
        target.description = target.description + '\n\n--- Merged from ---\n' + '\n'.join(merged_descriptions)
    if merged_notes:
        existing_notes = target.notes or ''
        target.notes = existing_notes + '\n' + '\n'.join(merged_notes) if existing_notes else '\n'.join(merged_notes)
    if earliest_date:
        target.planned_date = earliest_date

    # Merge workers
    for wid in merged_workers:
        w = Monteur.query.get(wid)
        if w and w not in target.workers:
            target.workers.append(w)

    # Merge assignments - add new ones, combine checklist items for matching sections/machines
    for ma in merged_assignments:
        # Check if target already has an assignment for this section/machine
        existing = None
        for ta in target.assignments:
            if ma['section_id'] and ta.section_id == ma['section_id']:
                existing = ta
                break
            if ma['machine_id'] and ta.machine_id == ma['machine_id']:
                existing = ta
                break

        if existing:
            # Merge checklist items into existing assignment
            existing_texts = {ci.text for ci in existing.checklist_items}
            next_order = max([ci.sort_order for ci in existing.checklist_items], default=0) + 1
            for ci_text in ma['checklist']:
                if ci_text not in existing_texts:
                    existing_texts.add(ci_text)
                    db.session.add(TWOChecklistItem(
                        two_id=target.id,
                        assignment_id=existing.id,
                        text=ci_text,
                        sort_order=next_order
                    ))
                    next_order += 1
        else:
            # Create new assignment
            new_assignment = TWOAssignment(
                two_id=target.id,
                section_id=ma['section_id'],
                machine_id=ma['machine_id'],
                description=ma['description'],
                sort_order=len(target.assignments)
            )
            db.session.add(new_assignment)
            db.session.flush()
            for j, ci_text in enumerate(ma['checklist']):
                db.session.add(TWOChecklistItem(
                    two_id=target.id,
                    assignment_id=new_assignment.id,
                    text=ci_text,
                    sort_order=j
                ))

    # Add orphan checklist items (not tied to any assignment)
    if orphan_checklists:
        existing_texts = {ci.text for ci in target.checklist_items}
        next_order = max([ci.sort_order for ci in target.checklist_items], default=0) + 1
        for ci_text in orphan_checklists:
            if ci_text not in existing_texts:
                existing_texts.add(ci_text)
                db.session.add(TWOChecklistItem(
                    two_id=target.id,
                    text=ci_text,
                    sort_order=next_order
                ))
                next_order += 1

    # Delete merged twos (not the target)
    for two in twos:
        if two.id != target.id:
            db.session.delete(two)

    db.session.flush()
    if not safe_commit():
        flash(_('Merge failed'), 'error')
        return redirect(url_for('two.two_list'))

    log_audit('merge', 'two', target.id, f'Merged {len(twos)} TWOs into {target.number}')
    flash(_('Merged {} TWOs into {}').format(len(twos), target.number), 'success')
    return redirect(url_for('two.two_detail', two_id=target.id))

@bp.route('/two/<int:two_id>/print')
@login_required
@role_required('admin', 'director', 'technician')
def two_print(two_id):
    two = TechnicalWorkOrder.query.get_or_404(two_id)
    return render_template('two_print.html', two=two)

@bp.route('/api/two/from-fault/<int:fault_id>')
@login_required
@role_required('admin', 'director', 'technician')
def two_from_fault(fault_id):
    """Get fault data for pre-filling TWO form"""
    f = FaultReport.query.get_or_404(fault_id)
    return jsonify({
        'id': f.id,
        'title': f.title,
        'description': f.description,
        'machine_id': f.machine_id,
        'machine_name': f.target_name,
        'section_id': f.machine.section_id if f.machine else None,
        'section_name': f.machine.section.name if f.machine and f.machine.section else '',
        'priority': f.priority,
        'reporter': f.reporter_label
    })

