"""Exercise a manual holiday refresh in a caller-selected disposable directory."""
import sys,json,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]));sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from PySide6.QtCore import QCoreApplication,QTimer
from journal_holidays import Holidays
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
if a.output.exists():raise ValueError('Use a fresh directory')
a.output.mkdir(parents=True);app=QCoreApplication([]);h=Holidays(a.output);result={}
def done(message):
    h.reload();result.update(message=message,years=sorted(h.years),passed=2026 in h.years and not (a.output/'holiday-cache/2027.json').exists());(a.output/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');app.quit()
h.finished.connect(done);QTimer.singleShot(0,lambda:h.update_year(2026));QTimer.singleShot(40000,lambda:done('超时'));app.exec();sys.exit(0 if result.get('passed') else 1)
