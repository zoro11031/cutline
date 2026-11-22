"""Every pass must retain each card's position and size."""
import unittest
from cutline import job, verify


class LaterPasses(unittest.TestCase):
    def test_last_pass_position_corruption_is_detected(self):
        checked = job.build(passes=2, offset_mm=(0,0))
        self.assertFalse(checked.blocked)
        head, commands = verify._split(checked.stream)
        start = max(i for i,c in enumerate(commands) if c.startswith(b'M')
                    and i+1 < len(commands) and commands[i+1].startswith(b'D'))
        for i in range(start,len(commands)):
            c=commands[i]
            if i > start and c.startswith(b'M'):
                break
            if c.startswith((b'M',b'D')):
                y,_,x=c[1:].partition(b',')
                commands[i]=c[:1]+str(int(y)-20).encode()+b','+x
        broken=verify._rejoin(head,commands)
        ok,rows=verify.expectations_met(broken, checked.grid,checked.marks,checked.settings,checked.device,checked.mode)
        self.assertFalse(ok)
        self.assertTrue(any(r.label=='card position error' and not r.ok for r in rows))

    def test_last_pass_size_corruption_is_detected(self):
        checked = job.build(passes=2, offset_mm=(0,0))
        self.assertFalse(checked.blocked)
        head, commands = verify._split(checked.stream)
        start = max(i for i,c in enumerate(commands) if c.startswith(b'M')
                    and i+1 < len(commands) and commands[i+1].startswith(b'D'))
        end = start+1
        while end<len(commands) and commands[end].startswith(b'D'):
            end+=1
        far=max(int(c[1:].partition(b',')[0]) for c in commands[start:end])
        for i in range(start,end):
            c=commands[i]
            y,_,x=c[1:].partition(b',')
            if int(y)==far:
                commands[i]=c[:1]+str(far+20).encode()+b','+x
        ok,rows=verify.expectations_met(verify._rejoin(head,commands), checked.grid,checked.marks,checked.settings,checked.device,checked.mode)
        self.assertFalse(ok)
        self.assertTrue(any(r.label=='card size error' and not r.ok for r in rows))
