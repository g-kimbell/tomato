arr = window.location.href.split('/');
page = arr[arr.length - 1];
document.write('\
<dl>\
    <dt>Versions</dt> \
    <dd><a href="../master/' + page + '">master</a></dd>\
    <dd><a href="../0.2.1/' + page + '">0.2.1</a></dd>\
</dl>\
');