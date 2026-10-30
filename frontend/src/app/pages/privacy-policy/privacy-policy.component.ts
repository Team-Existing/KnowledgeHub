import { Component, OnInit } from '@angular/core'
import { RouterLink } from '@angular/router'
import { IconComponent } from '../../components/icon/icon.component'
import { hostedProviders, ModelService } from '../../services/model.service'

@Component({
  selector: 'app-privacy-policy',
  imports: [RouterLink, IconComponent],
  templateUrl: './privacy-policy.component.html',
  styleUrl: './privacy-policy.component.css'
})
export class PrivacyPolicyComponent implements OnInit {
  /** Services this server's hosted models send text to; empty when every model is local */
  hosted: string[] = []

  constructor(private models: ModelService) {}

  async ngOnInit() {
    try { this.hosted = hostedProviders((await this.models.getCatalog()).models) } catch { /* show the local text */ }
  }
}
