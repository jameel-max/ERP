document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('select[data-shelf-filter-store]').forEach(function (shelfSelect) {
        const storeSelect = document.getElementById(shelfSelect.dataset.shelfFilterStore);
        if (!storeSelect) return;

        const options = Array.from(shelfSelect.options).map(function (option) {
            return option.cloneNode(true);
        });
        const placeholder = options.find(function (option) { return option.value === ''; });

        function refreshShelves() {
            const currentValue = shelfSelect.value;
            const storeId = storeSelect.value;
            shelfSelect.replaceChildren();
            if (placeholder) shelfSelect.appendChild(placeholder.cloneNode(true));

            let keptCurrent = false;
            options.forEach(function (option) {
                if (!option.value || String(option.dataset.storeId) !== String(storeId)) return;
                const copy = option.cloneNode(true);
                if (copy.value === currentValue) {
                    copy.selected = true;
                    keptCurrent = true;
                }
                shelfSelect.appendChild(copy);
            });
            if (!keptCurrent) shelfSelect.value = '';
        }

        storeSelect.addEventListener('change', refreshShelves);
        refreshShelves();
    });
});
