import logging
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.db import transaction
from django.db.models import Count
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, DetailView, FormView, TemplateView, UpdateView, View
from zentral.contrib.inventory.forms import EnrollmentSecretForm
from zentral.contrib.inventory.models import MetaBusinessUnit
from zentral.contrib.mdm.events import send_device_notification, send_mbu_device_notifications
from zentral.contrib.mdm.forms import DeviceSearchForm, UploadConfigurationProfileForm
from zentral.contrib.mdm.models import (MetaBusinessUnitPushCertificate,
                                        EnrolledDevice,
                                        DEPDevice, DEPEnrollmentSession, DEPProfile,
                                        OTAEnrollment, OTAEnrollmentSession,
                                        KernelExtensionPolicy, MDMEnrollmentPackage, ConfigurationProfile)
from zentral.utils.osx_package import get_standalone_package_builders

logger = logging.getLogger('zentral.contrib.mdm.views.management')


# Meta business units


class MetaBusinessUnitListView(LoginRequiredMixin, TemplateView):
    template_name = "mdm/metabusinessunit_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["mdm"] = True
        context["mbu_list"] = sorted(set(
            mbupc.meta_business_unit
            for mbupc in MetaBusinessUnitPushCertificate.objects.select_related("meta_business_unit").all()
        ), key=lambda mbu: mbu.name)
        return context


class MetaBusinessUnitDetailView(LoginRequiredMixin, DetailView):
    model = MetaBusinessUnit
    template_name = "mdm/metabusinessunit_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data()
        context["mdm"] = True
        mbu = context["object"]
        context["dep_profile_list"] = (DEPProfile.objects.select_related("virtual_server")
                                                         .filter(enrollment_secret__meta_business_unit=mbu)
                                                         .annotate(num_devices=Count("depdevice"))
                                                         .order_by("name", "pk"))
        context["ota_enrollment_list"] = (OTAEnrollment.objects.filter(enrollment_secret__meta_business_unit=mbu)
                                                               .order_by("name", "pk"))
        context["kext_policy_list"] = (KernelExtensionPolicy.objects.filter(meta_business_unit=mbu,
                                                                            trashed_at__isnull=True)
                                                                    .order_by("pk"))
        context["enrollment_package_list"] = (MDMEnrollmentPackage.objects.filter(meta_business_unit=mbu,
                                                                                  trashed_at__isnull=True)
                                                                          .order_by("builder", "pk"))
        existing_enrollment_package_builders = [ep.builder for ep in context["enrollment_package_list"]]
        create_enrollment_package_url = reverse("mdm:create_enrollment_package", args=(mbu.pk,))
        context["create_enrollment_package_links"] = [("{}?builder={}".format(create_enrollment_package_url, k),
                                                       v.name)
                                                      for k, v in get_standalone_package_builders().items()
                                                      if k not in existing_enrollment_package_builders]
        context["configuration_profile_list"] = (ConfigurationProfile.objects.filter(meta_business_unit=mbu,
                                                                                     trashed_at__isnull=True)
                                                                             .order_by("payload_description", "pk"))
        return context


# Devices


class DevicesView(LoginRequiredMixin, TemplateView):
    template_name = "mdm/device_list.html"

    def get(self, request, *args, **kwargs):
        self.form = DeviceSearchForm(request.GET)
        self.form.is_valid()
        self.devices = list(self.form.fetch_devices())
        if len(self.devices) == 1:
            return HttpResponseRedirect(reverse("mdm:device", args=(self.devices[0]["serial_number"],)))
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["mdm"] = True
        ctx["form"] = self.form
        ctx["devices"] = self.devices
        ctx["devices_count"] = len(self.devices)
        bc = [(None, "MDM")]
        if not self.form.is_initial():
            bc.extend([(reverse("mdm:devices"), "Devices"),
                       (None, "Search")])
        else:
            bc.extend([(None, "Devices")])
        ctx["breadcrumbs"] = bc
        return ctx


class DeviceView(LoginRequiredMixin, TemplateView):
    template_name = "mdm/device_info.html"

    def get_context_data(self, **kwargs):
        serial_number = kwargs["serial_number"]
        ctx = super().get_context_data(**kwargs)
        ctx["mdm"] = True
        ctx["serial_number"] = serial_number
        # enrolled devices
        ctx["enrolled_devices"] = EnrolledDevice.objects.filter(serial_number=serial_number).order_by("-updated_at")
        ctx["enrolled_devices_count"] = ctx["enrolled_devices"].count()
        # dep device?
        try:
            ctx["dep_device"] = DEPDevice.objects.get(serial_number=serial_number)
        except DEPDevice.DoesNotExist:
            pass
        # dep enrollment sessions
        ctx["dep_enrollment_sessions"] = DEPEnrollmentSession.objects.filter(
            enrollment_secret__serial_numbers__contains=[serial_number]
        ).order_by("-updated_at")
        ctx["dep_enrollment_sessions_count"] = ctx["dep_enrollment_sessions"].count()
        # ota enrollment sessions
        ctx["ota_enrollment_sessions"] = OTAEnrollmentSession.objects.filter(
            enrollment_secret__serial_numbers__contains=[serial_number]
        ).order_by("-updated_at")
        ctx["ota_enrollment_sessions_count"] = ctx["ota_enrollment_sessions"].count()
        return ctx


class PokeEnrolledDeviceView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        enrolled_device = get_object_or_404(EnrolledDevice, pk=kwargs["pk"])
        send_device_notification(enrolled_device)
        messages.info(request, "Device poked!")
        return HttpResponseRedirect(reverse("mdm:device", args=(enrolled_device.serial_number,)))


class EnrolledDeviceArtifactsView(LoginRequiredMixin, DetailView):
    model = EnrolledDevice
    template_name = "mdm/enrolled_device_artifacts.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["mdm"] = True
        context["installed_device_artifacts"] = sorted(self.object.installeddeviceartifact_set.all(),
                                                       key=lambda ida: ida.created_at, reverse=True)
        context["device_artifact_commands"] = sorted(self.object.deviceartifactcommand_set.all(),
                                                     key=lambda dac: dac.id, reverse=True)
        return context


# kernel extension policies


class CreateKernelExtensionPolicyView(LoginRequiredMixin, CreateView):
    model = KernelExtensionPolicy
    fields = "__all__"

    def dispatch(self, request, *args, **kwargs):
        self.meta_business_unit = get_object_or_404(MetaBusinessUnit, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["mdm"] = True
        context["meta_business_unit"] = self.meta_business_unit
        return context

    def form_valid(self, form):
        existing_kext_policies = (KernelExtensionPolicy.objects.select_for_update()
                                                               .filter(meta_business_unit=self.meta_business_unit))
        # there should be at most a trashed one.
        try:
            instance = existing_kext_policies[0]
        except IndexError:
            pass
        else:
            form.instance = instance
        kext_policy = form.save(commit=False)
        kext_policy.meta_business_unit = self.meta_business_unit
        kext_policy.trashed_at = None
        kext_policy.save()
        transaction.on_commit(lambda: send_mbu_device_notifications(kext_policy.meta_business_unit))
        return HttpResponseRedirect(kext_policy.get_absolute_url())


class KernelExtensionPolicyView(LoginRequiredMixin, DetailView):
    model = KernelExtensionPolicy

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["mdm"] = True
        return context


class UpdateKernelExtensionPolicyView(LoginRequiredMixin, UpdateView):
    model = KernelExtensionPolicy
    fields = "__all__"

    def dispatch(self, request, *args, **kwargs):
        self.meta_business_unit = get_object_or_404(MetaBusinessUnit, pk=kwargs["mbu_pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["mdm"] = True
        context["meta_business_unit"] = self.meta_business_unit
        return context

    def form_valid(self, form):
        kext_policy = form.save(commit=False)
        kext_policy.meta_business_unit = self.meta_business_unit
        kext_policy.save()
        transaction.on_commit(lambda: send_mbu_device_notifications(kext_policy.meta_business_unit))
        return HttpResponseRedirect(kext_policy.get_absolute_url())


class TrashKernelExtensionPolicyView(LoginRequiredMixin, DeleteView):
    model = KernelExtensionPolicy

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["mdm"] = True
        return context

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        self.object.trashed_at = timezone.now()
        self.object.save()
        transaction.on_commit(lambda: send_mbu_device_notifications(self.object.meta_business_unit))
        return HttpResponseRedirect(reverse("mdm:mbu", args=(self.object.meta_business_unit.pk,)))


# Enrollment Packages


class CreateEnrollmentPackageView(LoginRequiredMixin, TemplateView):
    template_name = "mdm/mdmenrollmentpackage_form.html"

    def dispatch(self, request, *args, **kwargs):
        standalone_builders = get_standalone_package_builders()
        self.meta_business_unit = get_object_or_404(MetaBusinessUnit, pk=kwargs["pk"])
        try:
            self.builder_key = request.GET["builder"]
            self.builder = standalone_builders[self.builder_key]
        except KeyError:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_forms(self):
        secret_form_kwargs = {"prefix": "secret",
                              "no_restrictions": True,
                              "meta_business_unit": self.meta_business_unit}
        enrollment_form_kwargs = {"meta_business_unit": self.meta_business_unit,
                                  "standalone": True}  # w/o dependencies. all in the package.
        if self.request.method == "POST":
            secret_form_kwargs["data"] = self.request.POST
            enrollment_form_kwargs["data"] = self.request.POST
        return (EnrollmentSecretForm(**secret_form_kwargs),
                self.builder.form(**enrollment_form_kwargs))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["mdm"] = True
        context["title"] = "Create enrollment package"
        context["meta_business_unit"] = self.meta_business_unit
        context["builder_name"] = self.builder.name
        if "secret_form" not in kwargs or "enrollment_form" not in kwargs:
            context["secret_form"], context["enrollment_form"] = self.get_forms()
        return context

    def forms_invalid(self, secret_form, enrollment_form):
        return self.render_to_response(self.get_context_data(secret_form=secret_form,
                                                             enrollment_form=enrollment_form))

    def forms_valid(self, secret_form, enrollment_form):
        # make secret
        secret = secret_form.save()
        # make enrollment
        enrollment = enrollment_form.save(commit=False)
        enrollment.version = 0
        enrollment.secret = secret
        enrollment.save()
        # MDM enrollment package
        mep = MDMEnrollmentPackage.objects.create(
            meta_business_unit=secret.meta_business_unit,
            builder=self.builder_key,
            enrollment_pk=enrollment.pk
        )
        # link from enrollment to mdm enrollment package, for config update propagation
        enrollment.distributor = mep
        enrollment.save()  # build package and package manifest via callback call
        transaction.on_commit(lambda: send_mbu_device_notifications(mep.meta_business_unit))
        return HttpResponseRedirect(mep.get_absolute_url())

    def post(self, request, *args, **kwargs):
        secret_form, enrollment_form = self.get_forms()
        if secret_form.is_valid() and enrollment_form.is_valid():
            return self.forms_valid(secret_form, enrollment_form)
        else:
            return self.forms_invalid(secret_form, enrollment_form)


class TrashEnrollmentPackageView(LoginRequiredMixin, DeleteView):
    model = MDMEnrollmentPackage

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["mdm"] = True
        return context

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        self.object.trashed_at = timezone.now()
        self.object.save()
        return HttpResponseRedirect(reverse("mdm:mbu", args=(self.object.meta_business_unit.pk,)))


# Configuration Profiles


class UploadConfigurationProfileView(LoginRequiredMixin, FormView):
    model = ConfigurationProfile
    form_class = UploadConfigurationProfileForm
    template_name = "mdm/configurationprofile_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.meta_business_unit = get_object_or_404(MetaBusinessUnit, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["meta_business_unit"] = self.meta_business_unit
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["mdm"] = True
        context["title"] = "upload a configuration profile"
        context["meta_business_unit"] = self.meta_business_unit
        return context

    def form_valid(self, form):
        self.configuration_profile = form.save()
        transaction.on_commit(lambda: send_mbu_device_notifications(self.meta_business_unit))
        return super().form_valid(form)

    def get_success_url(self):
        return self.configuration_profile.get_absolute_url()


class TrashConfigurationProfileView(LoginRequiredMixin, DeleteView):
    model = ConfigurationProfile

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["mdm"] = True
        return context

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        self.object.trashed_at = timezone.now()
        self.object.save()
        transaction.on_commit(lambda: send_mbu_device_notifications(self.object.meta_business_unit))
        return HttpResponseRedirect(reverse("mdm:mbu", args=(self.object.meta_business_unit.pk,)))
