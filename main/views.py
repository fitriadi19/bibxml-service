"""View functions for citation browse GUI."""

from urllib.parse import quote_plus, unquote_plus

from django.db.models.query import QuerySet
from django.http.response import HttpResponseNotFound, Http404
from django.http.response import HttpResponseRedirect, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.conf import settings
from django.views.generic.list import ListView
from django.views.generic.list import MultipleObjectTemplateResponseMixin
from django.contrib import messages

from bibxml import error_views

from .exceptions import RefNotFoundError
from .models import RefData
from .indexed import get_indexed_ref, list_refs, list_doctypes
from .indexed import search_refs_relaton_struct
from .external import get_doi_ref
from .util import BaseCitationSearchView


shared_context = dict(
    known_datasets=settings.KNOWN_DATASETS,
    indexed_datasets=[
        ds
        for ds in settings.KNOWN_DATASETS
        if ds not in settings.EXTERNAL_DATASETS],
    external_datasets=settings.EXTERNAL_DATASETS,
    authoritative_datasets=settings.AUTHORITATIVE_DATASETS,
    snapshot=settings.SNAPSHOT,
)
"""Shared context passed to GUI templates."""


def home(request, dataset_id=None, ref=None):
    non_empty_datasets = (
        RefData.objects.values_list('dataset', flat=True).
        distinct())

    total_indexed_citations = RefData.objects.count()

    browsable_datasets = [
        ds_id
        for ds_id in shared_context['indexed_datasets']
        if ds_id in non_empty_datasets]

    return render(request, 'browse/home.html', dict(
        **shared_context,
        total_indexed_citations=total_indexed_citations,
        browsable_datasets=browsable_datasets,
        doctypes=list_doctypes(),
    ))


# Browsing by document ID
# =======================

def browse_citation_by_docid(request, doctype=None, docid=None):
    if doctype and docid:
        parsed_docid = unquote_plus(docid)
        citations = search_refs_relaton_struct({
            'docid': [{
                'type': doctype,
                'id': parsed_docid,
            }],
        }, {
            'docid': {
                'type': doctype,
                'id': parsed_docid,
            },
        })
        num_citations = len(citations)
        if num_citations == 1:
            citation = citations[0]
            return render(request, 'browse/citation_details.html', dict(
                dataset_id=citation.dataset,
                ref=citation.ref,
                data=citation.body,
                **shared_context,
            ))
        elif num_citations == 0:
            return HttpResponseNotFound(
                "Citation with docid.type {} and docid.id {} "
                "was not found in indexed sources".format(
                    doctype,
                    docid,
                ))
        else:
            return HttpResponseNotFound(
                "Multiple citations with docid.type {} and docid.id {} "
                "were found in indexed sources".format(
                    doctype,
                    docid
                ))
    else:
        # Faciliates searching by doctype via a regular HTML form.

        doctype, docid = request.GET.get('doctype'), request.GET.get('docid')

        if not doctype or not docid:
            return HttpResponseBadRequest("Missing document type and/or ID")

        citations = search_refs_relaton_struct({
            'docid': [{
                'type': doctype,
                'id': docid,
            }],
        }, {
            'docid': {
                'type': doctype,
                'id': docid,
            },
        })
        if len(citations) == 1:
            return redirect(
                'browse_citation_by_docid',
                doctype,
                quote_plus(docid))
        else:
            messages.error(
                request,
                "No reliable match for a citation "
                "matching doctype “{}” and ID “{}” "
                "among indexed datasets ({} matches).".format(
                    doctype,
                    docid,
                    len(citations)))
            return HttpResponseRedirect(request.headers.get('referer', '/'))


class CitationSearchResultListView(MultipleObjectTemplateResponseMixin,
                                   BaseCitationSearchView):

    template_name = 'browse/search_citations.html'
    show_all_by_default = True

    def get_context_data(self, **kwargs):
        return dict(
            **super().get_context_data(**kwargs),
            **shared_context,
        )


# External sources
# ================

def external_dataset(request, dataset_id):
    return render(request, 'browse/dataset.html', dict(
        dataset_id=dataset_id,
        **shared_context,
    ))


def browse_external_reference(request, dataset_id, ref=None):
    if ref:
        parsed_ref = unquote_plus(ref)

        if dataset_id == 'doi':
            try:
                data = get_doi_ref(parsed_ref)
            except Exception:
                return error_views.server_error(request)
            else:
                return render(request, 'browse/citation_details.html', dict(
                    dataset_id=dataset_id,
                    ref=ref,
                    data=data,
                    **shared_context,
                ))
        else:
            return HttpResponseBadRequest("Unsupported external dataset ID")

    else:
        # Faciliates searching via a regular HTML form.

        ref = request.GET.get('ref')

        if not ref:
            return HttpResponseBadRequest(
                "Missing dataset ID and/or reference")

        origin = request.headers.get('referer', '/')

        if dataset_id not in settings.EXTERNAL_DATASETS:
            messages.error(
                request,
                "Unknown external dataset {}".format(dataset_id))

        if dataset_id == 'doi':
            ref = request.GET.get('ref')
            if ref:
                try:
                    get_doi_ref(ref)
                except RuntimeError as exc:
                    messages.error(
                        request,
                        "Couldn’t retrieve citation: {}".format(
                            str(exc)))
                else:
                    return redirect(
                        'browse_citation',
                        dataset_id,
                        quote_plus(ref))
            else:
                messages.error(request, "Missing reference to fetch {}")
        else:
            messages.error(
                request,
                "Unsupported external dataset {}".format(dataset_id))

        # If we’re here, it must’ve failed
        return HttpResponseRedirect(origin)


# Browsing by dataset (semi-internal)
# ===================================

def browse_indexed_reference(request, dataset_id, ref):
    parsed_ref = unquote_plus(ref)

    try:
        if dataset_id == 'doi':
            try:
                data = get_doi_ref(parsed_ref)
            except Exception:
                return error_views.server_error(request)
        else:
            data = get_indexed_ref(dataset_id, parsed_ref)

    except RefNotFoundError:
        raise Http404(
            "Requested reference “{}” "
            "could not be found in dataset “{}” "
            "(or external source is unavailable)".format(
                parsed_ref,
                dataset_id))

    else:
        return render(request, 'browse/citation_details.html', dict(
            dataset_id=dataset_id,
            ref=ref,
            data=data,
            **shared_context,
        ))


class IndexedDatasetCitationListView(ListView):
    model = RefData
    paginate_by = 20
    template_name = 'browse/dataset.html'

    def get_queryset(self) -> QuerySet[RefData]:
        return list_refs(self.kwargs['dataset_id'])

    def get_context_data(self, **kwargs):
        return dict(
            **super().get_context_data(**kwargs),
            dataset_id=self.kwargs['dataset_id'],
            **shared_context,
        )
