import os, re, sys, json, shutil, tempfile, threading, webbrowser
from pathlib import Path
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.lib.colors import white, black
from reportlab.lib.utils import simpleSplit

APP_NAME = 'Solicitud de Sobretiempo - Tremond'
BOSSES = ['Cristian Rios', 'Quique Abanto']


def app_root():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))


def user_data_dir():
    base = Path(os.environ.get('APPDATA', Path.home())) / 'Tremond' / 'SolicitudSobretiempo'
    base.mkdir(parents=True, exist_ok=True)
    return base


def parse_sctr(path):
    text = '\n'.join((p.extract_text() or '') for p in PdfReader(str(path)).pages)
    # Each insured person is printed as a single name + ID + ID type + row number.
    records = []
    for name, ident, kind in re.findall(r'([A-ZÁÉÍÓÚÜÑ� ]{4,}?)\s*(\d{8,12})\s*(DNI|CEX)\d+', text):
        name = ' '.join(name.split()).strip().replace('�', 'Ñ')
        if name and name not in {'ASEGURADO', 'CONSTANCIA DE ASEGURAMIENTO'}:
            records.append({'name': name.title(), 'dni': ident, 'kind': kind})
    unique = {r['dni']: r for r in records}
    if not unique:
        raise ValueError('No se encontraron trabajadores. Selecciona el PDF SCTR completo emitido para el mes.')
    return sorted(unique.values(), key=lambda x: x['name'])


def date_options():
    today = date.today()
    start = today - timedelta(days=730)
    end = today + timedelta(days=365)
    values = []
    d = start
    while d <= end:
        values.append(d.strftime('%d/%m/%Y'))
        d += timedelta(days=1)
    return values


def parse_date(value):
    return datetime.strptime(value, '%d/%m/%Y').date()


def scheduled_start(work_date, worker):
    if work_date.weekday() == 5:
        return 13 * 60 + 30
    if work_date.weekday() == 6:
        return 17 * 60 + 30
    upper = worker.upper()
    if 'JOSUE' in upper and 'ALVA' in upper:
        return 18 * 60 + 30
    return 17 * 60 + 30


def fmt_time(minutes):
    return f'{minutes // 60:02d}:{minutes % 60:02d}'


def generate_pdf(fields, output_path, management_decision=None, management_at=None):
    root = app_root()
    template = root / 'assets' / 'plantilla.pdf'
    sign = root / 'assets' / 'firma.png'
    management_sign = root / 'assets' / 'firma_gerencia.png'
    if not template.exists():
        raise FileNotFoundError(f'No se encuentra la plantilla: {template}')
    reader = PdfReader(str(template))
    page = reader.pages[0]
    width, height = float(page.mediabox.width), float(page.mediabox.height)
    temp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
    temp.close()
    c = canvas.Canvas(temp.name, pagesize=(width, height))

    def put(x, top, text, max_width=300, font_size=8.5, mask_x=None):
        text = str(text)
        size = font_size
        while size > 6 and c.stringWidth(text, 'Helvetica', size) > max_width:
            size -= .25
        baseline = height - top - size + 1.5
        c.setFillColor(white)
        left = x - 1 if mask_x is None else mask_x
        c.rect(left, baseline - 5.5, max_width + (x - left) + 1, size + 7, fill=1, stroke=0)
        c.setFillColor(black)
        c.setFont('Helvetica', size)
        c.drawString(x, baseline, text)

    # Cover only the blank underlines; original template artwork and wording stay intact.
    put(203, 119, fields['request_date'], 165)
    put(190, 161, fields['boss'], 120)
    put(202, 203, fields['worker'], 240, 8)
    put(142, 224, fields['dni'], 180)
    put(150, 246, fields['role'], 220)
    put(221, 287, fields['overtime_date'], 210)
    put(183, 308, fields['start_time'], 205)
    put(193, 329, fields['end_time'], 205)
    put(214, 350, fields['duration'], 205)

    reason_lines = simpleSplit(fields['reason'].strip(), 'Helvetica', 8.2, 470)
    for i, line in enumerate(reason_lines):
        put(98, 392 + i * 21, line, 480, 8.2, mask_x=84)
    justification_lines = simpleSplit(fields['justification'].strip(), 'Helvetica', 8.2, 470)
    for i, line in enumerate(justification_lines):
        put(98, 481 + i * 21, line, 480, 8.2, mask_x=84)

    # Mark the selected day type with X as requested.
    type_x = {'Día laborable': 89.4, 'Día de descanso': 173.6, 'Feriado': 272.0}[fields['day_type']]
    c.setFillColor(black)
    c.setFont('Helvetica-Bold', 10)
    c.drawCentredString(type_x, height - 545.5, 'X')

    # Keep the supplied horizontal signature stamp inside the requester signature space.
    if sign.exists():
        c.drawImage(ImageReader(str(sign)), 145, 160, width=80, height=43, preserveAspectRatio=True, mask='auto', anchor='c')
    # The selected manager is also recorded under the requester signature.
    put(156, 678, fields['boss'], 130, 8.5, mask_x=151)
    if management_decision in ('Aprobado', 'Denegado') and management_sign.exists():
        # The supplied blue signature sits on the blank Gerencia signature line.
        c.drawImage(ImageReader(str(management_sign)), 256, 70, width=78, height=45,
                    preserveAspectRatio=True, mask='auto', anchor='c')
        decision_color = (0.08, 0.38, 0.22) if management_decision == 'Aprobado' else (0.65, 0.12, 0.12)
        c.setFillColorRGB(*decision_color)
        c.setFont('Helvetica-Bold', 7.5)
        label = f'DECISIÓN GERENCIA: {management_decision.upper()}'
        if management_at:
            try:
                stamp = datetime.fromisoformat(management_at.replace('Z', '+00:00')).astimezone(ZoneInfo('America/Lima'))
                label += f' · {stamp:%d/%m/%Y %H:%M}'
            except ValueError:
                pass
        c.drawString(229, 48, label)
    c.save()
    overlay = PdfReader(temp.name)
    page.merge_page(overlay.pages[0])
    writer = PdfWriter()
    writer.add_page(page)
    with open(output_path, 'wb') as f:
        writer.write(f)
    os.unlink(temp.name)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry('780x760')
        self.minsize(720, 650)
        self.configure(bg='#f4f6fa')
        self.records = []
        self.data_dir = user_data_dir()
        self.state_file = self.data_dir / 'config.json'
        self.sctr_file = self.data_dir / 'sctr-actual.pdf'
        self._build_ui()
        self._load_people()
        self._refresh_calculations()

    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TFrame', background='#f4f6fa')
        style.configure('TLabel', background='#f4f6fa', font=('Segoe UI', 10))
        style.configure('Header.TLabel', background='#173d78', foreground='white', font=('Segoe UI', 16, 'bold'), padding=14)
        style.configure('Hint.TLabel', background='#f4f6fa', foreground='#46566f', font=('Segoe UI', 9))
        style.configure('TButton', font=('Segoe UI', 10), padding=7)
        style.configure('Primary.TButton', background='#174a8b', foreground='white')
        style.configure('TCombobox', padding=5)
        header = ttk.Label(self, text='SOLICITUD DE AUTORIZACIÓN DE SOBRETIEMPO', style='Header.TLabel', anchor='w')
        header.pack(fill='x')
        wrap = ttk.Frame(self, padding=(22, 13))
        wrap.pack(fill='both', expand=True)
        ttk.Label(wrap, text='Completa los campos y genera el PDF sobre la plantilla original.', style='Hint.TLabel').grid(row=0, column=0, columnspan=4, sticky='w', pady=(0, 8))
        self.vars = {k: tk.StringVar() for k in ['request_date','boss','worker','dni','role','overtime_date','start_time','end_time','duration','day_type']}
        self.date_values = date_options()
        self._field(wrap, 1, 0, 'Fecha de solicitud', 'request_date', self.date_values, width=18)
        self._field(wrap, 1, 2, 'Jefe solicitante', 'boss', BOSSES, width=22)
        self._field(wrap, 2, 0, 'Técnico (según SCTR)', 'worker', [], width=36)
        self._field(wrap, 2, 2, 'DNI / documento', 'dni', [], width=18, readonly=True)
        self._field(wrap, 3, 0, 'Cargo', 'role', ['Técnico','Técnico de mantenimiento','Ayudante técnico','Supervisor'], width=28)
        self._field(wrap, 3, 2, 'Fecha del sobretiempo', 'overtime_date', self.date_values, width=18)
        self._field(wrap, 4, 0, 'Hora de inicio (automática)', 'start_time', [], width=18, readonly=True)
        self._field(wrap, 4, 2, 'Hora de término', 'end_time', [], width=18)
        self._field(wrap, 5, 0, 'Total de sobretiempo', 'duration', [], width=18, readonly=True)
        self._field(wrap, 5, 2, 'Tipo de día', 'day_type', ['Día laborable','Día de descanso','Feriado'], width=22)
        self.vars['request_date'].set(date.today().strftime('%d/%m/%Y'))
        self.vars['boss'].set(BOSSES[0])
        self.vars['role'].set('Técnico')
        self.vars['overtime_date'].set(date.today().strftime('%d/%m/%Y'))
        self.vars['day_type'].set('Día de descanso' if date.today().weekday() == 6 else 'Día laborable')
        ttk.Label(wrap, text='Motivo del sobretiempo (texto libre)').grid(row=11, column=0, columnspan=4, sticky='w', pady=(9, 3))
        self.reason = tk.Text(wrap, height=3, wrap='word', font=('Segoe UI', 10), relief='solid', borderwidth=1)
        self.reason.grid(row=12, column=0, columnspan=4, sticky='ew')
        ttk.Label(wrap, text='Justificación operativa (texto libre)').grid(row=13, column=0, columnspan=4, sticky='w', pady=(9, 3))
        self.justification = tk.Text(wrap, height=3, wrap='word', font=('Segoe UI', 10), relief='solid', borderwidth=1)
        self.justification.grid(row=14, column=0, columnspan=4, sticky='ew')
        actions = ttk.Frame(wrap)
        actions.grid(row=15, column=0, columnspan=4, sticky='ew', pady=(13, 2))
        ttk.Button(actions, text='Actualizar SCTR del mes…', command=self.update_sctr).pack(side='left')
        ttk.Button(actions, text='Ver salida y reglas', command=self.show_rules).pack(side='left', padx=8)
        ttk.Button(actions, text='Generar PDF…', style='Primary.TButton', command=self.save_pdf).pack(side='right')
        self.status = ttk.Label(wrap, text='', style='Hint.TLabel')
        self.status.grid(row=16, column=0, columnspan=4, sticky='w', pady=(7, 0))
        for col in (1,3): wrap.columnconfigure(col, weight=1)
        self.worker_combo.bind('<<ComboboxSelected>>', self._worker_changed)
        self.end_combo.bind('<<ComboboxSelected>>', self._refresh_calculations)
        self.date_combos['overtime_date'].bind('<<ComboboxSelected>>', self._date_changed)

    def _field(self, parent, row, col, label, key, values, width=25, readonly=False):
        ttk.Label(parent, text=label).grid(row=row*2-1, column=col, sticky='w', pady=(5,0))
        if key == 'worker':
            self.worker_combo = ttk.Combobox(parent, textvariable=self.vars[key], values=values, state='readonly', width=width)
            widget = self.worker_combo
        elif key == 'end_time':
            self.end_combo = ttk.Combobox(parent, textvariable=self.vars[key], values=values, state='readonly', width=width)
            widget = self.end_combo
        else:
            widget = ttk.Combobox(parent, textvariable=self.vars[key], values=values, state='readonly', width=width)
        widget.grid(row=row*2, column=col, sticky='ew', pady=(2,1), padx=(0,14 if col==0 else 0))
        if key in ('request_date','overtime_date'): self.date_combos = getattr(self, 'date_combos', {}); self.date_combos[key] = widget

    def _load_people(self):
        try:
            if self.sctr_file.exists():
                self.records = parse_sctr(self.sctr_file)
            else:
                self.records = parse_sctr(app_root() / 'assets' / 'sctr-inicial.pdf')
            self.worker_combo['values'] = [r['name'] for r in self.records]
            if self.records:
                self.worker_combo.current(0)
                self._worker_changed()
            self.status.configure(text=f'SCTR cargado: {len(self.records)} trabajadores. Puedes cambiarlo cada mes.')
        except Exception as e:
            self.status.configure(text='No se pudo leer el SCTR. Usa “Actualizar SCTR del mes”.')
            messagebox.showwarning(APP_NAME, str(e))

    def update_sctr(self):
        path = filedialog.askopenfilename(title='Selecciona el SCTR vigente de este mes', filetypes=[('PDF','*.pdf')])
        if not path: return
        try:
            records = parse_sctr(path)
            shutil.copy2(path, self.sctr_file)
            self.records = records
            self.worker_combo['values'] = [r['name'] for r in records]
            self.worker_combo.current(0)
            self._worker_changed()
            self.status.configure(text=f'SCTR actualizado: {len(records)} trabajadores. El cambio queda guardado para los próximos usos.')
            self._save_config()
        except Exception as e:
            messagebox.showerror('No se pudo actualizar el SCTR', str(e))

    def _worker_changed(self, event=None):
        worker = self.vars['worker'].get()
        person = next((r for r in self.records if r['name'] == worker), None)
        self.vars['dni'].set(f"{person['dni']} ({person['kind']})" if person and person['kind'] == 'CEX' else (person['dni'] if person else ''))
        self._refresh_calculations()

    def _date_changed(self, event=None):
        try:
            d = parse_date(self.vars['overtime_date'].get())
            self.vars['day_type'].set('Día de descanso' if d.weekday() == 6 else 'Día laborable')
        except Exception: pass
        self._refresh_calculations()

    def _refresh_calculations(self, event=None):
        try:
            d = parse_date(self.vars['overtime_date'].get())
        except Exception:
            return
        start = scheduled_start(d, self.vars['worker'].get())
        self.vars['start_time'].set(fmt_time(start))
        options = [fmt_time(m) for m in range(start + 15, 24*60, 15)]
        self.end_combo['values'] = options
        if options and self.vars['end_time'].get() not in options:
            self.vars['end_time'].set(options[0])
        try:
            end = self.vars['end_time'].get()
            eh, em = map(int, end.split(':'))
            delta = eh*60 + em - start
            self.vars['duration'].set(f'{delta//60:02d} h {delta%60:02d} min' if delta > 0 else '')
        except Exception:
            self.vars['duration'].set('')

    def _save_config(self):
        self.state_file.write_text(json.dumps({'boss':self.vars['boss'].get(),'role':self.vars['role'].get()}, ensure_ascii=False, indent=2), encoding='utf-8')

    def save_pdf(self):
        if not self.vars['worker'].get():
            messagebox.showerror(APP_NAME, 'Selecciona un técnico del SCTR.')
            return
        if not self.reason.get('1.0','end').strip() or not self.justification.get('1.0','end').strip():
            messagebox.showerror(APP_NAME, 'Completa el motivo y la justificación.')
            return
        if not self.vars['end_time'].get():
            messagebox.showerror(APP_NAME, 'Selecciona la hora de término.')
            return
        path = filedialog.asksaveasfilename(title='Guardar solicitud en PDF', initialdir=str(Path.home() / 'Documents'), initialfile=f"Solicitud_Sobretiempo_{self.vars['worker'].get().replace(' ','_')}_{self.vars['overtime_date'].get().replace('/','-')}.pdf", defaultextension='.pdf', filetypes=[('PDF','*.pdf')])
        if not path: return
        fields = {k:v.get() for k,v in self.vars.items()}
        fields['reason'] = self.reason.get('1.0','end').strip()
        fields['justification'] = self.justification.get('1.0','end').strip()
        try:
            generate_pdf(fields, path)
            self._save_config()
            messagebox.showinfo(APP_NAME, f'PDF generado correctamente:\n{path}')
            try: os.startfile(path)
            except Exception: pass
        except Exception as e:
            messagebox.showerror('Error al generar el PDF', str(e))

    def show_rules(self):
        messagebox.showinfo('Horarios configurados', 'De lunes a viernes: sobretiempo desde las 5:30 p. m. (Josué Alva desde las 6:30 p. m.).\nSábados: desde la 1:30 p. m.\nLa jornada regular indicada se considera de 8:00 a. m. El total se calcula hasta la hora de término seleccionada.')


if __name__ == '__main__':
    App().mainloop()

















