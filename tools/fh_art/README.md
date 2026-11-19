# Frontier Hunter results graphic

`season2_template.html` is the Season 2 results art (1080 px wide, rendered at 2x for Discord).
Numbers, names and leader art are hand-filled from `python3 tools/fh_results.py <run>` output.

Render: put the unit pictures next to the html (unit_ills_full_<id>.png for the champion,
unit_ills_thum_<id>.png for the others - from the PC folder
D:\User\Pictures\Brave Frontier assets\21900\assets\content\unit\img), then
    node shot.js FH_Season2_Results.png 2        (reads season2_template.html renamed to s2.html)
Rules: ADMIN and tester accounts are left off the graphic and everyone below moves up.
