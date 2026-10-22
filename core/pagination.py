from django.core.paginator import Paginator


def paginate(request, queryset, per_page=30):
    """Paginate a list while keeping active search/filter parameters in links."""
    paginator = Paginator(queryset, per_page)
    page_obj = paginator.get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    prefix = f'?{params.urlencode()}&' if params else '?'
    first = max(1, page_obj.number - 2)
    last = min(paginator.num_pages, page_obj.number + 2)
    page_links = [
        {'number': number, 'url': f'{prefix}page={number}', 'current': number == page_obj.number}
        for number in range(first, last + 1)
    ]
    return {
        'page_obj': page_obj,
        'page_links': page_links,
        'result_start': page_obj.start_index() if paginator.count else 0,
        'result_end': page_obj.end_index(),
        'total_count': paginator.count,
        'previous_url': f'{prefix}page={page_obj.previous_page_number()}' if page_obj.has_previous() else None,
        'next_url': f'{prefix}page={page_obj.next_page_number()}' if page_obj.has_next() else None,
    }
